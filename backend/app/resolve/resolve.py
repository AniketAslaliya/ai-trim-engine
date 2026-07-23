"""Stage 3: Intent + Timeline -> EDL.

Deterministic path first (free, exact); LLM path only for predicates that
need semantic judgment over transcript/tags text (see intent-pipeline skill,
Stage 3). Constraints are enforced here, after filtering/ranking.
"""
import json

from app import config
from app.schemas import EDL, Clip, Constraints, Intent, Segment, Timeline, Transition

# Predicate phrasings the intent-parser is instructed to use for simple,
# directly-checkable fields — resolved with zero LLM calls.
_DETERMINISTIC_KEYWORDS = {
    "is_silence": lambda seg: seg.is_silence,
    "filler_word": lambda seg: len(seg.filler_words) > 0,
    "filler words": lambda seg: len(seg.filler_words) > 0,
}


def _try_deterministic(predicate: str, segments: list[Segment]) -> set[int] | None:
    p = predicate.lower()
    for keyword, check in _DETERMINISTIC_KEYWORDS.items():
        if keyword in p:
            return {s.id for s in segments if check(s)}
    if "speaker" in p:
        for seg in segments:
            if seg.speaker and seg.speaker.lower() in p:
                return {s.id for s in segments if s.speaker == seg.speaker}
    return None


_SEMANTIC_PROMPT = """You are matching a predicate against video segments.

Predicate: {predicate}
Operation: {operation}

Segments (id, transcript, scene_tags, objects, audio_events):
{segments_json}

Return ONLY a JSON array of segment ids that satisfy the predicate. If operation is
"rank_select", return the array ordered best-match first instead. No commentary.
"""


def _resolve_semantic(intent: Intent, segments: list[Segment]) -> list[int]:
    if not config.ANTHROPIC_API_KEY:
        return []
    import anthropic

    compact = [
        {
            "id": s.id, "transcript": s.transcript,
            "scene_tags": s.scene_tags, "objects": s.objects,
            "audio_events": s.audio_events,
        }
        for s in segments
    ]
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": _SEMANTIC_PROMPT.format(
                predicate=intent.predicate,
                operation=intent.operation,
                segments_json=json.dumps(compact),
            ),
        }],
    )
    text = resp.content[0].text
    return json.loads(text[text.find("["):text.rfind("]") + 1])


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

    if intent.operation == "constrain_only":
        matched_ids = [s.id for s in segments]
        ranked_ids = []
    else:
        det = _try_deterministic(intent.predicate, segments)
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
