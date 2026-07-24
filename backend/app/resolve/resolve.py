"""Stage 3: Intent + Timeline -> EDL.

Deterministic path first (free, exact); LLM path only for predicates that
need semantic judgment over transcript/tags text (see intent-pipeline skill,
Stage 3). Constraints are enforced here, after filtering/ranking.
"""
import json
import math

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
    "is_duplicate_take": lambda seg: seg.is_duplicate_take,
}

# Padding around each filler word's exact timestamp so the cut doesn't clip
# the tail of the word before it or the head of the word after it.
_FILLER_PAD_SEC = 0.08

# rank_select without an explicit duration budget ("keep the funniest
# moments" with no length given) still needs to actually select, not just
# keep everything the ranker considered relevant — default to roughly half,
# floor of 1, so "highlight reel"-style asks are selective by default.
_DEFAULT_RANK_SELECT_FRACTION = 0.5


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


def _compact_segments(segments: list[Segment]) -> list[dict]:
    return [
        {
            "id": s.id, "transcript": s.transcript,
            "scene_tags": s.scene_tags, "objects": s.objects,
            "action_tags": s.action_tags, "audio_events": s.audio_events,
            "is_silence": s.is_silence, "has_filler_words": bool(s.filler_words),
            "is_duplicate_take": s.is_duplicate_take,
        }
        for s in segments
    ]


_SEMANTIC_PROMPT = """You are matching a predicate against video segments.

Predicate: {predicate}
Operation: {operation}

Segments (id, transcript, scene_tags, objects, action_tags, audio_events, is_silence, has_filler_words, is_duplicate_take):
{segments_json}

Return the segment ids that satisfy the predicate, ordered best-match / most-relevant
first (order matters even for a plain filter — it's used if the result needs to be
trimmed to a duration budget, and for "reorder" it becomes the actual output sequence).
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

    data = llm.complete_json(
        None,
        _SEMANTIC_PROMPT.format(
            predicate=intent.predicate,
            operation=intent.operation,
            segments_json=json.dumps(_compact_segments(segments)),
        ),
        _SEMANTIC_RESULT_SCHEMA,
        max_tokens=1500,
    )
    return data["segment_ids"]


_RANK_PROMPT = """Rank these video segments by how well they match the criterion below,
BEST match first. Only include segments with at least some relevance — exclude ones with
none. Order matters: it drives which segments survive a duration budget and/or the final
sequence of the edit.

Criterion: {predicate}

Segments (id, transcript, scene_tags, objects, action_tags, audio_events, is_silence):
{segments_json}
"""


def _rank_segments(predicate: str, segments: list[Segment]) -> list[int]:
    """Always requests an ordering, regardless of Intent.operation — used both
    for real rank_select/reorder intents and as the picking strategy behind
    constrain_only when a duration budget needs to choose the *best* content
    instead of chopping whatever comes first chronologically."""
    if not config.llm_configured():
        return []
    data = llm.complete_json(
        None,
        _RANK_PROMPT.format(predicate=predicate, segments_json=json.dumps(_compact_segments(segments))),
        _SEMANTIC_RESULT_SCHEMA,
        max_tokens=1500,
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


def _reordered_clips(order_ids: list[int], kept_ids: set[int], segments: list[Segment]) -> list[Clip]:
    """For operation="reorder": one clip per kept segment, in the given
    (non-chronological) order — no adjacency merging, since order is the
    whole point (e.g. "build a 3-act story", "start with the strongest hook")."""
    by_id = {s.id: s for s in segments}
    return [
        Clip(segment_ids=[sid], start=by_id[sid].start, end=by_id[sid].end)
        for sid in order_ids if sid in kept_ids
    ]


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

    ranked_ids: list[int] = []

    if intent.operation in ("rank_select", "reorder"):
        ranked_ids = _rank_segments(intent.predicate, segments)
        if intent.operation == "rank_select" and intent.constraints.max_duration_sec is None and ranked_ids:
            cap = max(1, math.ceil(len(ranked_ids) * _DEFAULT_RANK_SELECT_FRACTION))
            ranked_ids = ranked_ids[:cap]
        matched_ids = sorted(ranked_ids)
    elif intent.operation == "constrain_only":
        matched_ids = [s.id for s in segments]
        if intent.constraints.max_duration_sec is not None:
            # A duration budget with no other filter still needs to pick the
            # *best* content, not just chop whatever comes first — rank by
            # the intent's own predicate (e.g. "keep the most important/
            # engaging parts") rather than leaving the order chronological.
            ranked_ids = _rank_segments(intent.predicate, segments)
    else:
        det = _try_deterministic(intent, segments)
        if det is not None:
            matched_ids = sorted(det)
        else:
            ranked_ids = _resolve_semantic(intent, segments)
            matched_ids = sorted(ranked_ids)

    if intent.operation == "constrain_only":
        # matched_ids is always the full segment set here by construction —
        # constrain_only has no independent match criterion of its own.
        kept_ids = set(matched_ids)
    elif intent.mode == "remove":
        # Nothing matched -> nothing to remove -> correctly a no-op (kept_ids
        # ends up as every segment). This is NOT the same situation as below.
        kept_ids = {s.id for s in segments if s.id not in matched_ids}
    else:
        # mode="keep" (filter/rank_select/reorder): matched_ids empty means
        # the predicate genuinely matched nothing in this video. Do NOT fall
        # back to "keep everything" — that silently misrepresents a real
        # non-match as a successful no-op edit. Let kept_ids stay empty so
        # the refusal below fires with an honest explanation instead.
        kept_ids = set(matched_ids)

    kept_ids_list = _apply_constraints(sorted(kept_ids), ranked_ids, segments, intent.constraints)
    kept_ids = set(kept_ids_list)

    if not kept_ids:
        raise ValueError(
            f"Nothing in this video matched \"{intent.predicate}\" — refusing to return an "
            "unedited video pretending it worked, or an empty one. Try a different prompt, "
            "or this may genuinely not be present in the footage."
        )

    if intent.operation == "reorder" and ranked_ids:
        clips = _reordered_clips(ranked_ids, kept_ids, segments)
    else:
        clips = _segments_to_clips(kept_ids, segments)

    transitions = [
        Transition(at_clip_boundary=i, type="audio_fade", duration_sec=0.03)
        for i in range(len(clips))
    ]

    kept_dur = sum(c.end - c.start for c in clips)
    summary = (
        f"{intent.operation} '{intent.predicate}': kept {len(kept_ids)}/{len(segments)} "
        f"segments, {len(clips)} clips, {kept_dur:.1f}s of {timeline.duration_sec:.1f}s original."
    )

    return EDL(video_id=timeline.video_id, clips=clips, transitions=transitions, summary=summary)
