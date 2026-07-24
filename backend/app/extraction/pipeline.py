"""Orchestrates extraction sub-steps into a single Timeline.

Segment boundaries = shots, further split at silence boundaries that fall
inside a shot. Each sub-step degrades independently (see intent-pipeline
skill) — a failed diarization or tagging call must not fail the run.

Reports progress and partial results via an optional callback so a
long-running extraction isn't a black box to whoever's waiting on it — a
blank "processing..." screen for a multi-minute video is a real reason
people abandon the app before it ever produces anything.
"""
import subprocess
from typing import Callable, Optional

from app import config
from app.extraction.scenes import detect_shots
from app.extraction.silence import detect_silence
from app.extraction.transcribe import transcribe
from app.extraction.visual_tags import tag_shot
from app.schemas import Segment, Timeline, Word

ProgressFn = Callable[[str, Timeline], None]


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


def build_timeline(video_id: str, video_path: str, on_progress: Optional[ProgressFn] = None) -> Timeline:
    duration = 0.0

    def report(msg: str, segments: Optional[list[Segment]] = None) -> None:
        if on_progress:
            on_progress(msg, Timeline(video_id=video_id, duration_sec=duration, segments=segments or []))

    report("Reading video metadata...")
    duration = _get_duration(video_path)

    report("Transcribing audio...")
    words = transcribe(video_path)

    report("Detecting silences...")
    silences = detect_silence(video_path)

    report("Detecting scene changes...")
    shots = detect_shots(video_path)

    segments: list[Segment] = []
    seg_id = 0
    total_shots = len(shots)
    for i, (shot_start, shot_end) in enumerate(shots):
        report(f"Analyzing shot {i + 1}/{total_shots}...", segments)
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
        report(f"Analyzed shot {i + 1}/{total_shots}", segments)

    return Timeline(video_id=video_id, duration_sec=duration, segments=segments)
