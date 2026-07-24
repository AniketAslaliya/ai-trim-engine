"""Multi-video composition: combine segments from several already-extracted
videos into one sequence, per a natural-language description of the desired
story/order — the "phase 2" cinematic capability on top of single-video
trimming.

Honest scope (see PRD): still not full frame-level CV match-cut detection
(optical flow / pose-tracking / motion-vector matching across shots) — that
remains a real, unproven-in-scope CV problem. What this DOES do at each
cross-video join: the LLM sees every candidate segment (tags + is_silence,
nothing pre-filtered) to decide WHICH segments and order satisfy the
instruction, then — once a sequence is chosen — for every cross-video join,
match_cut.find_best_trim searches nearby candidate cut points inside each
segment's own bounds via real vision-LLM frame comparisons, so the actual
cut point lands on the best-matching moment (e.g. skipping past a
preparatory/wind-up motion) instead of just accepting the segment's original
boundary. Never removes/trims content beyond what the instruction asked for
— the search only adjusts WHERE inside already-selected content to cut, not
WHAT content is included.
"""
import json

from app import config, llm, match_cut
from app.schemas import EDL, Clip, Segment, Timeline, Transition

_COMPOSE_PROMPT = """You are combining segments from MULTIPLE videos into one sequence, per the user's description of the story/order they want.

Instruction: {prompt}

Segments from every video (each tagged with which video it came from, in is_silence: true/false — a
segment with no speech can still be visually essential, e.g. a silent hand-movement shot; it is NOT
pre-filtered out for you):
{segments_json}

Critical rule: only remove/trim/exclude segments the instruction actually asked you to remove. If the
instruction is purely about ORDER or MATCH-CUTTING ("combine these into one sequence with a match cut"),
do not drop silent segments, "boring" segments, or anything else on your own initiative — include
everything relevant to the requested story, in the requested order. Only exclude segments when the
instruction explicitly asks for a removal (e.g. "cut the silences," "skip the boring parts").

Pick the segments to include, in the FINAL desired order — order matters, this is the actual output sequence.

Match cuts — when the instruction asks for a match cut (or "natural" cut) across two videos, look for the
SAME EVENT/ACTION straddling the join: e.g. one clip's action_tags/transcript show a motion or gesture
CONCLUDING (a hand movement finishing, a person sitting down) right where another clip's action_tags/
transcript show the SAME KIND of motion CONTINUING or STARTING. Prefer joining an action-ending segment to
an action-starting segment of the same action type — that is what makes a cut read as continuous motion
across two different videos, not just cutting on a similar background/object. Report which action you
matched on. If a segment shows a "wind-up"/preparatory motion (about to do something, not yet doing it)
right before a segment showing the completed motion, prefer landing the cut AFTER the wind-up so the
preparation isn't shown as dead time in the middle of the sequence — but ONLY skip that wind-up material
if it was already part of what the instruction asked you to trim; don't invent a removal the user never
asked for. When there's a genuine choice between multiple segments that would equally satisfy the
instruction, prefer the option whose scene_tags/objects/action_tags most resemble the segment immediately
before it. Do not sacrifice the user's actual instruction just to force a match — a good match cut is a
bonus when available, never a requirement that overrides what they asked for.

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


# A trim search is never allowed to shrink a clip below this — a "better"
# cut point that would leave almost nothing of the segment is a search
# artifact, not a real improvement.
_MIN_TRIM_CLIP_SEC = 0.3


def compose_sequence(prompt: str, timelines: dict[str, Timeline], video_paths: dict[str, str] | None = None) -> EDL:
    if not config.llm_configured():
        raise ValueError(
            "Combining multiple videos requires an LLM call to work out the sequence/story order — "
            "no LLM provider is configured."
        )

    # Every segment is a candidate, including silent ones — a segment with no
    # speech can still be the exact visual content a match-cut or reorder
    # request needs (e.g. a silent hand-movement shot). Silently pre-filtering
    # "no dialogue" segments here used to mean they were removed even when
    # the user never asked for any silence to be cut — see PRD §9a.
    compact = []
    for vid, tl in timelines.items():
        for s in tl.segments:
            compact.append({
                "video_id": vid, "segment_id": s.id, "transcript": s.transcript,
                "is_silence": s.is_silence,
                "scene_tags": s.scene_tags, "objects": s.objects, "action_tags": s.action_tags,
            })

    if not compact:
        raise ValueError("None of the provided videos have any content to combine.")

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
        idx = len(clips) - 1
        transition = Transition(at_clip_boundary=idx, type="audio_fade", duration_sec=0.03)
        transitions.append(transition)
        if prev_seg is not None and prev_vid != vid:
            note = f"{prev_vid[:8]}→{vid[:8]}"
            score = None
            if video_paths and prev_vid in video_paths and vid in video_paths:
                prev_clip = clips[idx - 1]
                # Search for the actual action-aligned cut point inside each
                # segment's own bounds (see match_cut.find_best_trim) instead
                # of accepting the segment boundary as-is — this is what lets
                # a preparatory/wind-up motion get trimmed away automatically
                # when a later point in the same segment matches better.
                refined = match_cut.find_best_trim(
                    video_paths[prev_vid], prev_clip.start, prev_seg.end,
                    video_paths[vid], seg.start, seg.end,
                )
                if refined["a_end"] - prev_clip.start >= _MIN_TRIM_CLIP_SEC:
                    prev_clip.end = refined["a_end"]
                if seg.end - refined["b_start"] >= _MIN_TRIM_CLIP_SEC:
                    clips[idx].start = refined["b_start"]
                score = refined
            if score and score["visual_score"] is not None:
                transition.visual_score = score["visual_score"]
                transition.visual_reason = score["visual_reason"]
                transition.audio_delta_db = score["audio_delta_db"]
                note += f" (visual {score['visual_score']}/10"
                if score["audio_delta_db"] is not None:
                    note += f", audio Δ{score['audio_delta_db']}dB"
                note += f": {score['visual_reason']})"
            else:
                note += f" (tag match: {_tag_overlap(prev_seg, seg)})"
            match_notes.append(note)
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


def build_manual_compose_edl(clips_in: list[tuple[str, float, float]], video_paths: dict[str, str]) -> EDL:
    """No-LLM path for the Premiere-style compose timeline: the frontend sends
    the full, already-decided (video_id, start, end) clip list in final
    order — reordering/trimming/deleting clips is a pure frontend timeline
    interaction, not something that needs re-asking the LLM. Still runs real
    match_cut scoring on every cross-video join so manual edits get the same
    genuine visual/audio continuity feedback as the auto-composed sequence.
    """
    if not clips_in:
        raise ValueError("No clips in the sequence — nothing to render.")

    clips: list[Clip] = []
    transitions: list[Transition] = []
    match_notes: list[str] = []
    prev_vid: str | None = None
    prev_end: float | None = None

    for vid, start, end in clips_in:
        clips.append(Clip(video_id=vid, segment_ids=[], start=start, end=end))
        transition = Transition(at_clip_boundary=len(clips) - 1, type="audio_fade", duration_sec=0.03)
        transitions.append(transition)
        if prev_vid is not None and prev_vid != vid and prev_vid in video_paths and vid in video_paths:
            score = match_cut.score_join(video_paths[prev_vid], prev_end, video_paths[vid], start)
            note = f"{prev_vid[:8]}→{vid[:8]}"
            if score["visual_score"] is not None:
                transition.visual_score = score["visual_score"]
                transition.visual_reason = score["visual_reason"]
                transition.audio_delta_db = score["audio_delta_db"]
                note += f" (visual {score['visual_score']}/10"
                if score["audio_delta_db"] is not None:
                    note += f", audio Δ{score['audio_delta_db']}dB"
                note += f": {score['visual_reason']})"
            match_notes.append(note)
        prev_vid, prev_end = vid, end

    total = sum(c.end - c.start for c in clips)
    match_summary = "; ".join(match_notes) if match_notes else "single video, no cross-video joins"
    summary = f"Manually edited to {len(clips)} clip(s), {total:.1f}s total. Cross-video joins: {match_summary}."
    return EDL(video_id="multi", clips=clips, transitions=transitions, summary=summary)
