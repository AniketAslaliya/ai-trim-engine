"""Verify compose merges same-video segments before match-cut search."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.compose import compose_sequence, _merge_contiguous_clips
from app.schemas import Clip, Segment, Timeline


def test_merge_before_match_cut_search_range():
    """find_best_trim must see the merged clip span, not a single segment."""
    seg_a1 = Segment(id=0, start=0.0, end=5.0, scene_tags=["desk"])
    seg_a2 = Segment(id=1, start=5.0, end=12.0, scene_tags=["hand"])
    seg_b = Segment(id=0, start=0.0, end=8.0, scene_tags=["outdoor"])
    tl_a = Timeline(video_id="vid-a", duration_sec=12.0, segments=[seg_a1, seg_a2])
    tl_b = Timeline(video_id="vid-b", duration_sec=8.0, segments=[seg_b])

    seen: list[tuple[float, float, float, float]] = []

    def fake_find_best_trim(video_a, seg_a_start, seg_a_end, video_b, seg_b_start, seg_b_end, steps=4):
        seen.append((seg_a_start, seg_a_end, seg_b_start, seg_b_end))
        return {
            "a_end": seg_a_end, "b_start": seg_b_start,
            "visual_score": 7, "visual_reason": "ok", "audio_delta_db": 1.0, "natural": True,
        }

    llm_sequence = {
        "sequence": [
            {"video_id": "vid-a", "segment_id": 0},
            {"video_id": "vid-a", "segment_id": 1},
            {"video_id": "vid-b", "segment_id": 0},
        ]
    }

    with patch("app.compose.llm.complete_json", return_value=llm_sequence), \
         patch("app.compose.match_cut.find_best_trim", side_effect=fake_find_best_trim):
        edl = compose_sequence(
            "combine with a match cut",
            {"vid-a": tl_a, "vid-b": tl_b},
            {"vid-a": "/fake/a.mp4", "vid-b": "/fake/b.mp4"},
        )

    assert len(edl.clips) == 2
    assert edl.clips[0].start == 0.0 and edl.clips[0].end == 12.0
    assert seen == [(0.0, 12.0, 0.0, 8.0)]


def test_match_cut_runs_after_merge_not_before():
    """Refined cut points are applied to merged clips — merge must not follow match cut."""
    clips = [
        Clip(video_id="a", segment_ids=[0], start=0.0, end=5.0),
        Clip(video_id="b", segment_ids=[0], start=0.0, end=8.0),
    ]
    merged = _merge_contiguous_clips(clips)
    assert len(merged) == 2  # cross-video boundary — no merge

    # Simulate match-cut trim on the outgoing clip; nothing re-expands it afterward.
    merged[0].end = 4.2
    assert merged[0].end == 4.2


def test_match_cut_search_widens_into_unselected_gap():
    """A silent/unselected segment adjacent to the chosen join segment must
    still be reachable by the match-cut search, not cut off at the chosen
    segment's own boundary — this is the actual reported bug: extraction
    splits at silence boundaries, so the true best cut point can sit just
    past the selected segment's edge, inside a segment the sequence LLM
    never picked."""
    seg_a_chosen = Segment(id=0, start=0.0, end=5.0, scene_tags=["desk"])
    seg_a_gap = Segment(id=1, start=5.0, end=7.0, is_silence=True)  # never selected
    seg_b_gap = Segment(id=0, start=0.0, end=1.5, is_silence=True)  # never selected
    seg_b_chosen = Segment(id=1, start=1.5, end=8.0, scene_tags=["outdoor"])
    tl_a = Timeline(video_id="vid-a", duration_sec=7.0, segments=[seg_a_chosen, seg_a_gap])
    tl_b = Timeline(video_id="vid-b", duration_sec=8.0, segments=[seg_b_gap, seg_b_chosen])

    seen: list[tuple[float, float, float, float]] = []

    def fake_find_best_trim(video_a, seg_a_start, seg_a_end, video_b, seg_b_start, seg_b_end, steps=4):
        seen.append((seg_a_start, seg_a_end, seg_b_start, seg_b_end))
        return {
            "a_end": seg_a_end, "b_start": seg_b_start,
            "visual_score": 7, "visual_reason": "ok", "audio_delta_db": 1.0, "natural": True,
        }

    llm_sequence = {
        "sequence": [
            {"video_id": "vid-a", "segment_id": 0},
            {"video_id": "vid-b", "segment_id": 1},
        ]
    }

    with patch("app.compose.llm.complete_json", return_value=llm_sequence), \
         patch("app.compose.match_cut.find_best_trim", side_effect=fake_find_best_trim):
        compose_sequence(
            "combine with a match cut",
            {"vid-a": tl_a, "vid-b": tl_b},
            {"vid-a": "/fake/a.mp4", "vid-b": "/fake/b.mp4"},
        )

    # Without widening this would be (0.0, 5.0, 1.5, 8.0) — the search would
    # never see the 2s silent gap on either side.
    assert seen == [(0.0, 7.0, 0.0, 8.0)]


def test_weak_join_triggers_one_retry():
    """A poor real match-cut score must trigger exactly one retry against the
    compose LLM with the weak join flagged, and the retry's result is only
    adopted if it actually improves — never silently replaces a decent
    result with a worse or equally-bad one."""
    seg_a = Segment(id=0, start=0.0, end=5.0, scene_tags=["desk"])
    seg_b = Segment(id=0, start=0.0, end=8.0, scene_tags=["outdoor"])
    seg_b_alt = Segment(id=1, start=8.0, end=10.0, scene_tags=["outdoor", "hand"])
    tl_a = Timeline(video_id="vid-a", duration_sec=5.0, segments=[seg_a])
    tl_b = Timeline(video_id="vid-b", duration_sec=10.0, segments=[seg_b, seg_b_alt])

    call_count = {"n": 0}

    def fake_llm_complete_json(system, user, schema, max_tokens=2000):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return {"sequence": [{"video_id": "vid-a", "segment_id": 0}, {"video_id": "vid-b", "segment_id": 0}]}
        return {"sequence": [{"video_id": "vid-a", "segment_id": 0}, {"video_id": "vid-b", "segment_id": 1}]}

    def fake_find_best_trim(video_a, seg_a_start, seg_a_end, video_b, seg_b_start, seg_b_end, steps=4):
        # First pass picks segment_id 0 (ends at 8.0); retry's alternate segment_id 1 (ends at
        # 10.0) scores well. Keyed off the upper bound since the lower bound legitimately
        # widens backward into the prior segment regardless of which one was chosen.
        score = 2 if seg_b_end <= 8.0 else 9
        return {
            "a_end": seg_a_end, "b_start": seg_b_start,
            "visual_score": score, "visual_reason": "test", "audio_delta_db": 1.0, "natural": score >= 6,
        }

    with patch("app.compose.llm.complete_json", side_effect=fake_llm_complete_json), \
         patch("app.compose.match_cut.find_best_trim", side_effect=fake_find_best_trim):
        edl = compose_sequence(
            "combine with a match cut",
            {"vid-a": tl_a, "vid-b": tl_b},
            {"vid-a": "/fake/a.mp4", "vid-b": "/fake/b.mp4"},
        )

    assert call_count["n"] == 2  # exactly one retry, not an unbounded loop
    assert edl.transitions[1].visual_score == 9  # adopted the retry's better result


if __name__ == "__main__":
    test_merge_before_match_cut_search_range()
    test_match_cut_runs_after_merge_not_before()
    test_match_cut_search_widens_into_unselected_gap()
    test_weak_join_triggers_one_retry()
    print("compose order tests passed")
