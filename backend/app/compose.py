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
from typing import Optional

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


def _merge_contiguous_clips(clips: list[Clip]) -> list[Clip]:
    """Collapses consecutive clips that are the SAME source video and
    genuinely adjacent in time into one clip — otherwise every internal
    segment boundary (e.g. a sentence/silence split from extraction) becomes
    an unnecessary extra cut/fade inside what should be one continuous shot.
    The single-video pipeline already does this (resolve.py's
    _segments_to_clips); compose_sequence didn't, which is what produced
    "extra cuts that aren't needed" whenever a chosen sequence happened to
    include several consecutive segments from the same video. A cross-video
    boundary is never merged (different video_id), so this can't interfere
    with match-cut joins.

    Must run BEFORE match-cut boundary search — if match cut ran on per-
    segment bounds first and merge ran after, the merge step could expand a
    clip back across segment gaps and undo the refined cut point, and the
    search range would be too narrow to find the real action-aligned join.
    """
    merged: list[Clip] = []
    for c in clips:
        prev = merged[-1] if merged else None
        if prev is not None and prev.video_id == c.video_id and abs(prev.end - c.start) < 1e-3:
            merged[-1] = Clip(
                video_id=c.video_id, segment_ids=prev.segment_ids + c.segment_ids,
                start=prev.start, end=c.end,
            )
        else:
            merged.append(c)
    return merged


def _clip_tag_union(clip: Clip, segments_by_key: dict[tuple[str, int], Segment]) -> Segment:
    """Merged tag view of every extraction segment covered by a clip."""
    scene: set[str] = set()
    objects: set[str] = set()
    actions: set[str] = set()
    for sid in clip.segment_ids:
        seg = segments_by_key.get((clip.video_id or "", sid))
        if seg is None:
            continue
        scene.update(seg.scene_tags)
        objects.update(seg.objects)
        actions.update(seg.action_tags)
    return Segment(
        id=-1, start=clip.start, end=clip.end,
        scene_tags=sorted(scene), objects=sorted(objects), action_tags=sorted(actions),
    )


# How far a join search is allowed to reach into the immediately adjacent
# original-timeline segment (even if that segment is silent or was never
# selected) to find a real candidate cut point. Bounded so a join can't
# wander arbitrarily far from the content the instruction actually chose —
# it only reclaims the ONE neighboring gap, not the whole video.
_JOIN_WIDEN_CAP_SEC = 3.0

# A join whose real visual match-cut score comes back below this is treated
# as a genuine miss worth one retry against the compose LLM, not silently
# accepted — see compose_sequence's single bounded retry pass.
_WEAK_JOIN_SCORE = 5


def _widen_join_bound(timeline: Optional[Timeline], clip: Clip, side: str) -> tuple[float, float]:
    """Extends one side of a cross-video join's search range into the
    immediately adjacent segment from that video's ORIGINAL extraction
    timeline — including a silent or unselected segment — so a real
    match-cut candidate that lands in a trimmed-out gap is still reachable
    by find_best_trim's search instead of being cut off at the chosen
    segment's own boundary. Capped by _JOIN_WIDEN_CAP_SEC. side="a" widens
    the clip's END forward (the outgoing clip of the join); side="b" widens
    the clip's START backward (the incoming clip of the join)."""
    if timeline is None:
        return (clip.start, clip.end)
    segments = sorted(timeline.segments, key=lambda s: s.start)
    if side == "a":
        for i, seg in enumerate(segments):
            if abs(seg.end - clip.end) < 1e-3:
                nxt = segments[i + 1] if i + 1 < len(segments) else None
                hi = min(nxt.end, clip.end + _JOIN_WIDEN_CAP_SEC) if nxt else clip.end
                return (clip.start, hi)
        return (clip.start, clip.end)
    for i, seg in enumerate(segments):
        if abs(seg.start - clip.start) < 1e-3:
            prev = segments[i - 1] if i > 0 else None
            lo = max(prev.start, clip.start - _JOIN_WIDEN_CAP_SEC) if prev else clip.start
            return (lo, clip.end)
    return (clip.start, clip.end)


def _drop_leading_silence(clips: list[Clip], segments_by_key: dict[tuple[str, int], Segment]) -> list[Clip]:
    """Trims dead air at the very start of the composed output — the one
    silence-handling exception applied automatically regardless of the
    prompt, matching the same rule single-video resolve.py applies (see
    resolve.py's _leading_silence_ids). Interior silence gaps chosen by the
    sequence LLM are left exactly as decided; only a leading run of clips
    that are ENTIRELY silence gets dropped, and never down to zero clips."""
    i = 0
    while i < len(clips) - 1:
        segs = [segments_by_key.get((clips[i].video_id or "", sid)) for sid in clips[i].segment_ids]
        if segs and all(s is not None and s.is_silence for s in segs):
            i += 1
        else:
            break
    return clips[i:]


def _apply_match_cuts(
    clips: list[Clip],
    video_paths: dict[str, str] | None,
    segments_by_key: dict[tuple[str, int], Segment] | None = None,
    timelines: dict[str, Timeline] | None = None,
) -> tuple[list[Transition], list[str], list[int]]:
    """Score and refine every cross-video join on the final merged clip list.
    Returns the transitions, human-readable notes, and the clip indices (by
    position of the boundary's incoming clip) whose real visual score came
    back weak — used by compose_sequence to decide whether a single retry
    against the LLM is worth it."""
    transitions: list[Transition] = []
    match_notes: list[str] = []
    weak_boundaries: list[int] = []
    for idx, clip in enumerate(clips):
        transition = Transition(at_clip_boundary=idx, type="audio_fade", duration_sec=0.03)
        transitions.append(transition)
        if idx == 0:
            continue
        prev_clip = clips[idx - 1]
        if prev_clip.video_id == clip.video_id:
            continue
        note = f"{prev_clip.video_id[:8]}→{clip.video_id[:8]}"
        score = None
        if video_paths and prev_clip.video_id in video_paths and clip.video_id in video_paths:
            a_lo, a_hi = _widen_join_bound(
                (timelines or {}).get(prev_clip.video_id or ""), prev_clip, "a"
            )
            b_lo, b_hi = _widen_join_bound(
                (timelines or {}).get(clip.video_id or ""), clip, "b"
            )
            refined = match_cut.find_best_trim(
                video_paths[prev_clip.video_id], a_lo, a_hi,
                video_paths[clip.video_id], b_lo, b_hi,
            )
            if refined["a_end"] - prev_clip.start >= _MIN_TRIM_CLIP_SEC:
                prev_clip.end = refined["a_end"]
            if clip.end - refined["b_start"] >= _MIN_TRIM_CLIP_SEC:
                clip.start = refined["b_start"]
            score = refined
        if score and score["visual_score"] is not None:
            transition.visual_score = score["visual_score"]
            transition.visual_reason = score["visual_reason"]
            transition.audio_delta_db = score["audio_delta_db"]
            note += f" (visual {score['visual_score']}/10"
            if score["audio_delta_db"] is not None:
                note += f", audio Δ{score['audio_delta_db']}dB"
            note += f": {score['visual_reason']})"
            if score["visual_score"] < _WEAK_JOIN_SCORE:
                weak_boundaries.append(idx)
        elif segments_by_key:
            note += f" (tag match: {_tag_overlap(_clip_tag_union(prev_clip, segments_by_key), _clip_tag_union(clip, segments_by_key))})"
        else:
            note += " (unscored)"
        match_notes.append(note)
    return transitions, match_notes, weak_boundaries


# One flagged-retry against the compose LLM is the entire feedback loop —
# bounded to a single extra call so a run of weak joins can't turn into an
# unbounded back-and-forth (see PRD §7 cost target).
_RETRY_PROMPT = """You previously produced this sequence for the instruction below, but real
frame-level scoring found that some cross-video joins don't read as a good match cut. Revise
the sequence to fix ONLY those specific joins — e.g. by swapping in an adjacent segment_id
from the same video at that boundary, or reordering slightly nearby — while keeping everything
else about the sequence, and the original instruction's intent, unchanged. Do not drop or add
content the instruction didn't ask for.

Instruction: {prompt}

All candidate segments (same as before):
{segments_json}

Your previous sequence, in order:
{previous_sequence_json}

Joins that scored poorly (prev clip -> next clip, with the real visual score/reason):
{weak_joins_json}

Return a revised sequence in the same format as before.
"""


def _build_clips_from_sequence(sequence: list[dict], segments_by_key: dict[tuple[str, int], Segment]) -> list[Clip]:
    clips: list[Clip] = []
    for item in sequence:
        vid, sid = item["video_id"], item["segment_id"]
        seg = segments_by_key.get((vid, sid))
        if seg is None:
            continue  # LLM referenced something that doesn't exist — skip rather than fail the whole compose
        clips.append(Clip(video_id=vid, segment_ids=[seg.id], start=seg.start, end=seg.end))
    return clips


def _retry_weak_joins(
    prompt: str, compact: list[dict], sequence: list[dict],
    clips: list[Clip], transitions: list[Transition], weak_boundaries: list[int],
) -> Optional[list[dict]]:
    weak_joins = []
    for idx in weak_boundaries:
        prev_clip, clip, t = clips[idx - 1], clips[idx], transitions[idx]
        weak_joins.append({
            "prev_video_id": prev_clip.video_id, "prev_segment_ids": prev_clip.segment_ids,
            "next_video_id": clip.video_id, "next_segment_ids": clip.segment_ids,
            "visual_score": t.visual_score, "reason": t.visual_reason,
        })
    try:
        data = llm.complete_json(
            None,
            _RETRY_PROMPT.format(
                prompt=prompt, segments_json=json.dumps(compact),
                previous_sequence_json=json.dumps(sequence), weak_joins_json=json.dumps(weak_joins),
            ),
            _COMPOSE_SCHEMA,
            max_tokens=2000,
        )
        return data["sequence"]
    except Exception:
        return None  # a failed retry just keeps the original result — never worse than not retrying


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

    clips = _build_clips_from_sequence(sequence, segments_by_key)
    if not clips:
        raise ValueError("None of the selected segments were valid — try a different description.")

    clips = _merge_contiguous_clips(clips)
    clips = _drop_leading_silence(clips, segments_by_key)
    transitions, match_notes, weak_boundaries = _apply_match_cuts(clips, video_paths, segments_by_key, timelines)

    if weak_boundaries and video_paths:
        revised_sequence = _retry_weak_joins(prompt, compact, sequence, clips, transitions, weak_boundaries)
        if revised_sequence:
            revised_clips = _build_clips_from_sequence(revised_sequence, segments_by_key)
            if revised_clips:
                revised_clips = _merge_contiguous_clips(revised_clips)
                revised_clips = _drop_leading_silence(revised_clips, segments_by_key)
                revised_transitions, revised_notes, revised_weak = _apply_match_cuts(
                    revised_clips, video_paths, segments_by_key, timelines
                )
                # Only adopt the retry if it actually reduced the number of
                # weak joins — a failed/no-better retry must not silently
                # replace a decent original result.
                if len(revised_weak) < len(weak_boundaries):
                    clips, transitions, match_notes = revised_clips, revised_transitions, revised_notes

    total = sum(c.end - c.start for c in clips)
    match_summary = "; ".join(match_notes) if match_notes else "single video, no cross-video joins"
    summary = (
        f"Combined {len(timelines)} video(s) into {len(clips)} clip(s), {total:.1f}s total. "
        f"Cross-video joins: {match_summary}."
    )
    return EDL(video_id="multi", clips=clips, transitions=transitions, summary=summary)


def build_manual_compose_edl(
    clips_in: list[tuple[str, float, float]],
    video_paths: dict[str, str],
    timelines: dict[str, Timeline] | None = None,
) -> EDL:
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
    for vid, start, end in clips_in:
        clips.append(Clip(video_id=vid, segment_ids=[], start=start, end=end))

    clips = _merge_contiguous_clips(clips)
    transitions, match_notes, _ = _apply_match_cuts(clips, video_paths, timelines=timelines)

    total = sum(c.end - c.start for c in clips)
    match_summary = "; ".join(match_notes) if match_notes else "single video, no cross-video joins"
    summary = f"Manually edited to {len(clips)} clip(s), {total:.1f}s total. Cross-video joins: {match_summary}."
    return EDL(video_id="multi", clips=clips, transitions=transitions, summary=summary)
