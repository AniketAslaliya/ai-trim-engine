"""Cumulative edit state per video: the composed set of KEPT time-ranges (in
original-video coordinates) across every edit applied to that video so far.

Each new edit's own keep-ranges are still computed fresh against the
untouched original Timeline (same resolve.py/manual.py logic as a one-shot
edit), then intersected with this stored state here. The source video is
always re-cut from the original file — never from a prior render — so
chained edits never compound ffmpeg re-encode quality loss; only the
*decision* of what to keep is cumulative, not the media itself.
"""
import json
import os

from app import config
from app.schemas import EDL, Clip, Transition


def _state_path(video_id: str) -> str:
    return str(config.video_dir(video_id) / "keep_ranges.json")


def load_keep_ranges(video_id: str, duration: float) -> list[tuple[float, float]]:
    try:
        with open(_state_path(video_id)) as f:
            data = json.load(f)
        return [(r[0], r[1]) for r in data]
    except FileNotFoundError:
        return [(0.0, duration)]


def save_keep_ranges(video_id: str, ranges: list[tuple[float, float]]) -> None:
    with open(_state_path(video_id), "w") as f:
        json.dump(ranges, f)


def reset_keep_ranges(video_id: str) -> None:
    """Called after a fresh/retried extraction so cumulative state never
    survives a re-extraction of the same video_id."""
    try:
        os.remove(_state_path(video_id))
    except FileNotFoundError:
        pass


def intersect_ranges(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> list[tuple[float, float]]:
    a, b = sorted(a), sorted(b)
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi - lo > 0.01:
            result.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return result


def ranges_to_edl(video_id: str, keep_ranges: list[tuple[float, float]], summary: str) -> EDL:
    clips = [Clip(segment_ids=[], start=s, end=e) for s, e in keep_ranges]
    transitions = [
        Transition(at_clip_boundary=i, type="audio_fade", duration_sec=0.03)
        for i in range(len(clips))
    ]
    return EDL(video_id=video_id, clips=clips, transitions=transitions, summary=summary)
