"""Multi-video composition: combine segments from several already-extracted
videos into one sequence, per a natural-language description of the desired
story/order — the "phase 2" cinematic capability on top of single-video
trimming.

Honest scope (see PRD): this does NOT implement true frame-level match-cut
detection (optical flow / pose / composition matching across shots) — that's
a real, unproven-in-scope CV problem. What this DOES do: feed the LLM every
candidate segment's visual tags (scene_tags/objects/action_tags, the same
ones the single-video pipeline already extracts) alongside the sequencing
instruction, explicitly ask it to prefer cutting between segments whose tags
resemble each other at cross-video join points, and report a computed tag-
overlap score for each cross-video cut so the "match" is inspectable, not a
black-box claim.
"""
import json

from app import config, llm
from app.schemas import EDL, Clip, Segment, Timeline, Transition

_COMPOSE_PROMPT = """You are combining segments from MULTIPLE videos into one sequence, per the user's description of the story/order they want.

Instruction: {prompt}

Segments from all videos (each tagged with which video it came from). Silence segments are already excluded:
{segments_json}

Pick the segments to include, in the FINAL desired order — order matters, this is the actual output sequence.

When there's a genuine choice between multiple segments that would equally satisfy the instruction at a
point where the sequence crosses from one video to another, prefer the option whose scene_tags/objects/
action_tags most resemble the segment immediately before it — this creates a "match cut" (the content
visually rhymes across the join, a real and valued editing technique). Do not sacrifice the user's actual
instruction just to force a match — a good match cut is a bonus when available, never a requirement that
overrides what they asked for.

Return the sequence as an array of {{"video_id": ..., "segment_id": ...}} objects, in final order.
"""

_COMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "sequence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "video_id": {"type": "string"},
                    "segment_id": {"type": "integer"},
                },
                "required": ["video_id", "segment_id"],
            },
        }
    },
    "required": ["sequence"],
}


def _tag_overlap(a: Segment, b: Segment) -> int:
    a_tags = set(a.scene_tags) | set(a.objects) | set(a.action_tags)
    b_tags = set(b.scene_tags) | set(b.objects) | set(b.action_tags)
    return len(a_tags & b_tags)


def compose_sequence(prompt: str, timelines: dict[str, Timeline]) -> EDL:
    if not config.llm_configured():
        raise ValueError(
            "Combining multiple videos requires an LLM call to work out the sequence/story order — "
            "no LLM provider is configured."
        )

    compact = []
    for vid, tl in timelines.items():
        for s in tl.segments:
            if s.is_silence:
                continue  # not useful as story content in a multi-video edit
            compact.append({
                "video_id": vid, "segment_id": s.id, "transcript": s.transcript,
                "scene_tags": s.scene_tags, "objects": s.objects, "action_tags": s.action_tags,
            })

    if not compact:
        raise ValueError("None of the provided videos have any non-silent content to combine.")

    data = llm.complete_json(
        None,
        _COMPOSE_PROMPT.format(prompt=prompt, segments_json=json.dumps(compact)),
        _COMPOSE_SCHEMA,
        max_tokens=2000,
    )
    sequence = data["sequence"]
    if not sequence:
        raise ValueError(
            "Could not work out a sequence from that description — try being more specific about "
            "the order or which parts of each video you want."
        )

    segments_by_key = {(vid, s.id): s for vid, tl in timelines.items() for s in tl.segments}

    clips: list[Clip] = []
    transitions: list[Transition] = []
    match_notes: list[str] = []
    prev_seg: Segment | None = None
    prev_vid: str | None = None

    for item in sequence:
        vid, sid = item["video_id"], item["segment_id"]
        seg = segments_by_key.get((vid, sid))
        if seg is None:
            continue  # LLM referenced something that doesn't exist — skip rather than fail the whole compose
        clips.append(Clip(video_id=vid, segment_ids=[seg.id], start=seg.start, end=seg.end))
        transitions.append(Transition(at_clip_boundary=len(clips) - 1, type="audio_fade", duration_sec=0.03))
        if prev_seg is not None and prev_vid != vid:
            overlap = _tag_overlap(prev_seg, seg)
            match_notes.append(f"{prev_vid[:8]}→{vid[:8]} (tag match: {overlap})")
        prev_seg, prev_vid = seg, vid

    if not clips:
        raise ValueError("None of the selected segments were valid — try a different description.")

    total = sum(c.end - c.start for c in clips)
    match_summary = "; ".join(match_notes) if match_notes else "single video, no cross-video joins"
    summary = (
        f"Combined {len(timelines)} video(s) into {len(clips)} clip(s), {total:.1f}s total. "
        f"Cross-video joins: {match_summary}."
    )
    return EDL(video_id="multi", clips=clips, transitions=transitions, summary=summary)
