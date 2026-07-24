"""Manual trim: user-drawn timeline selections -> EDL, entirely deterministic.

No LLM call anywhere in this path — it's pure interval arithmetic on the
video's known duration, which is why it's instant and free compared to the
Intent/resolve.py path. Kept separate from resolve.py because it doesn't
touch Intent or the Timeline's segment content at all, only time ranges.
"""
from app.edit_state import ranges_to_edl
from app.schemas import EDL, Timeline, TimeRange


def _merge_ranges(ranges: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _invert_ranges(remove: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Returns the kept intervals — the complement of `remove` within [0, duration]."""
    keep = []
    cursor = 0.0
    for start, end in remove:
        start, end = max(start, 0.0), min(end, duration)
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        keep.append((cursor, duration))
    return keep


def build_manual_edl(timeline: Timeline, remove_ranges: list[TimeRange]) -> EDL:
    merged_remove = _merge_ranges([(r.start, r.end) for r in remove_ranges])
    keep = [(s, e) for s, e in _invert_ranges(merged_remove, timeline.duration_sec) if e - s > 0.01]

    if not keep:
        raise ValueError("Manual trim would remove the entire video — refusing to render an empty result.")

    removed_dur = sum(e - s for s, e in merged_remove)
    summary = (
        f"Manual trim: removed {len(merged_remove)} selection(s) totaling {removed_dur:.1f}s "
        f"of {timeline.duration_sec:.1f}s original."
    )
    return ranges_to_edl(timeline.video_id, keep, summary)
