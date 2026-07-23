"""Orchestrates extraction sub-steps into a single Timeline.

Segment boundaries = shots, further split at silence boundaries that fall
inside a shot. Each sub-step degrades independently (see intent-pipeline
skill) — a failed diarization or tagging call must not fail the run.
"""
import subprocess

from app import config
from app.extraction.scenes import detect_shots
from app.extraction.silence import detect_silence
from app.extraction.transcribe import transcribe
from app.extraction.visual_tags import tag_shot
from app.schemas import Segment, Timeline, Word


def _get_duration(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def _split_shot_by_silence(
    shot_start: float, shot_end: float, silences: list[tuple[float, float]]
) -> list[tuple[float, float, bool]]:
    """Splits one shot into [(start, end, is_silence), ...] sub-intervals."""
    overlapping = [
        (max(s, shot_start), min(e, shot_end))
        for s, e in silences
        if s < shot_end and e > shot_start
    ]
    overlapping.sort()

    pieces: list[tuple[float, float, bool]] = []
    cursor = shot_start
    for s, e in overlapping:
        if s > cursor:
            pieces.append((cursor, s, False))
        pieces.append((max(s, cursor), e, True))
        cursor = max(cursor, e)
    if cursor < shot_end:
        pieces.append((cursor, shot_end, False))
    return pieces or [(shot_start, shot_end, False)]


def _words_in(words: list[Word], start: float, end: float) -> list[Word]:
    return [w for w in words if w.start >= start and w.end <= end]


def build_timeline(video_id: str, video_path: str) -> Timeline:
    duration = _get_duration(video_path)
    words = transcribe(video_path)
    silences = detect_silence(video_path)
    shots = detect_shots(video_path)

    segments: list[Segment] = []
    seg_id = 0
    for shot_start, shot_end in shots:
        scene_tags, objects = tag_shot(video_path, shot_start, shot_end)
        for sub_start, sub_end, is_silence in _split_shot_by_silence(shot_start, shot_end, silences):
            seg_words = [] if is_silence else _words_in(words, sub_start, sub_end)
            transcript = " ".join(w.text for w in seg_words)
            filler = [w for w in seg_words if w.text.lower().strip(".,!?") in config.FILLER_WORDS]
            segments.append(Segment(
                id=seg_id,
                start=sub_start,
                end=sub_end,
                transcript=transcript,
                words=seg_words,
                is_silence=is_silence,
                shot_boundary=(sub_start == shot_start),
                scene_tags=scene_tags,
                objects=objects,
                filler_words=filler,
            ))
            seg_id += 1

    return Timeline(video_id=video_id, duration_sec=duration, segments=segments)
