"""Stage 3: Intent + Timeline -> EDL.

Deterministic path first (free, exact); LLM path only for predicates that
need semantic judgment over transcript/tags text (see intent-pipeline skill,
Stage 3). Constraints are enforced here, after filtering/ranking.
"""
import json

from app import config, llm
from app.resolve.manual import build_manual_edl
from app.schemas import EDL, Clip, Constraints, Intent, Segment, Timeline, TimeRange, Transition

# Fields resolvable by direct boolean check on the Timeline — zero LLM calls.
# Routed on Intent.target_signal (structured), never on predicate wording —
# the predicate is free text the intent-parser can phrase however it wants,
# so matching against it directly is fragile (see backend/README.md notes).
_BOOLEAN_SIGNALS = {
    "is_silence": lambda seg: seg.is_silence,
    "filler_words": lambda seg: len(seg.filler_words) > 0,
}

# Padding around each filler word's exact timestamp so the cut doesn't clip
# the tail of the word before it or the head of the word after it.
_FILLER_PAD_SEC = 0.08


def _filler_word_ranges(segments: list[Segment]) -> list[TimeRange]:
    """Exact per-word cut ranges, not whole segments. A segment's transcript
    ("so today we're gonna, um, talk about") is several seconds long and
    mostly real speech — removing filler_words at segment granularity would
    delete all of it just because one "um" is inside it. Segments already
    carry word-level timestamps for exactly this reason (see
    .claude/skills/timeline-schema); use them."""
    ranges = []
    for seg in segments:
        for w in seg.filler_words:
            ranges.append(TimeRange(
                start=max(w.start - _FILLER_PAD_SEC, seg.start),
                end=min(w.end + _FILLER_PAD_SEC, seg.end),
            ))
    return ranges


def _try_deterministic(intent: Intent, segments: list[Segment]) -> set[int] | None:
    signals = set(intent.target_signal)

    if signals and signals <= set(_BOOLEAN_SIGNALS):
        return {
            s.id for s in segments
            if any(_BOOLEAN_SIGNALS[sig](s) for sig in signals)
        }

    if signals == {"speaker"}:
        p = intent.predicate.lower()
        for seg in segments:
            if seg.speaker and seg.speaker.lower() in p:
                return {s.id for s in segments if s.speaker == seg.speaker}

    return None


_SEMANTIC_PROMPT = """You are matching a predicate against video segments.

Predicate: {predicate}
Operation: {operation}

Segments (id, transcript, scene_tags, objects, audio_events, is_silence, has_filler_words):
{segments_json}

Return the segment ids that satisfy the predicate. If operation is "rank_select",
order the ids best-match first instead.
"""

# Wrapped in an object (not a bare array) because Anthropic's tool input_schema
# requires an object at the root — keeping one schema shape for both providers
# avoids provider-specific branching here.
_SEMANTIC_RESULT_SCHEMA = {
    "type": "object",
    "properties": {"segment_ids": {"type": "array", "items": {"type": "integer"}}},
    "required": ["segment_ids"],
}


def _resolve_semantic(intent: Intent, segments: list[Segment]) -> list[int]:
    if not config.llm_configured():
        return []

    compact = [
        {
            "id": s.id, "transcript": s.transcript,
            "scene_tags": s.scene_tags, "objects": s.objects,
            "audio_events": s.audio_events,
            "is_silence": s.is_silence, "has_filler_words": bool(s.filler_words),
        }
        for s in segments
    ]
    data = llm.complete_json(
        None,
        _SEMANTIC_PROMPT.format(
            predicate=intent.predicate,
            operation=intent.operation,
            segments_json=json.dumps(compact),
        ),
        _SEMANTIC_RESULT_SCHEMA,
        max_tokens=1000,
    )
    return data["segment_ids"]


def _apply_constraints(kept_ids: list[int], ranked_ids: list[int], segments: list[Segment], constraints: Constraints) -> list[int]:
    if constraints.max_duration_sec is None:
        return kept_ids
    by_id = {s.id: s for s in segments}
    order = ranked_ids if ranked_ids else kept_ids
    total = 0.0
    result = []
    for sid in order:
        seg = by_id[sid]
        dur = seg.end - seg.start
        if total + dur > constraints.max_duration_sec:
            continue
        result.append(sid)
        total += dur
    return sorted(result)


def _segments_to_clips(kept_ids: set[int], segments: list[Segment]) -> list[Clip]:
    """Merges contiguous kept segments into clips to avoid a cut per segment."""
    ordered = sorted((s for s in segments if s.id in kept_ids), key=lambda s: s.start)
    clips: list[Clip] = []
    for seg in ordered:
        if clips and abs(clips[-1].end - seg.start) < 1e-3 and seg.id - 1 in kept_ids:
            clips[-1] = Clip(segment_ids=clips[-1].segment_ids + [seg.id], start=clips[-1].start, end=seg.end)
        else:
            clips.append(Clip(segment_ids=[seg.id], start=seg.start, end=seg.end))
    return clips


def resolve(intent: Intent, timeline: Timeline) -> EDL:
    segments = timeline.segments

    if intent.operation == "filter" and intent.mode == "remove" and set(intent.target_signal) == {"filler_words"}:
        ranges = _filler_word_ranges(segments)
        if not ranges:
            return EDL(
                video_id=timeline.video_id,
                clips=[Clip(segment_ids=[s.id for s in segments], start=0.0, end=timeline.duration_sec)],
                summary="No filler words detected — nothing to remove.",
            )
        edl = build_manual_edl(timeline, ranges)
        edl.summary = f"Removed {len(ranges)} filler word(s). {edl.summary}"
        return edl

    if intent.operation == "constrain_only":
        matched_ids = [s.id for s in segments]
        ranked_ids = []
    else:
        det = _try_deterministic(intent, segments)
        if det is not None:
            matched_ids = sorted(det)
            ranked_ids = []
        else:
            ranked_ids = _resolve_semantic(intent, segments)
            matched_ids = sorted(ranked_ids)

    if intent.mode == "remove" and intent.operation != "constrain_only":
        kept_ids = {s.id for s in segments if s.id not in matched_ids}
    else:
        kept_ids = set(matched_ids) if matched_ids else {s.id for s in segments}

    kept_ids_list = _apply_constraints(sorted(kept_ids), ranked_ids, segments, intent.constraints)
    kept_ids = set(kept_ids_list)

    if not kept_ids:
        raise ValueError(
            f"Resolved edit would remove everything (predicate: '{intent.predicate}'). "
            "Refusing to render an empty video — check the predicate or source footage."
        )
    if kept_ids == {s.id for s in segments} and intent.operation != "constrain_only":
        # Nothing was actually cut — surface this rather than silently no-op rendering.
        pass

    clips = _segments_to_clips(kept_ids, segments)
    transitions = [
        Transition(at_clip_boundary=i, type="audio_fade", duration_sec=0.03)
        for i in range(len(clips))
    ]

    kept_dur = sum(c.end - c.start for c in clips)
    summary = (
        f"{intent.mode.capitalize()} '{intent.predicate}': kept {len(kept_ids)}/{len(segments)} "
        f"segments, {len(clips)} clips, {kept_dur:.1f}s of {timeline.duration_sec:.1f}s original."
    )

    return EDL(video_id=timeline.video_id, clips=clips, transitions=transitions, summary=summary)
