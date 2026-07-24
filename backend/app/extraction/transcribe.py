"""Word-level transcription via faster-whisper. Runs once per video, cached by the caller."""
import subprocess
from functools import lru_cache

from app import config
from app.schemas import Word


@lru_cache(maxsize=1)
def _model():
    from faster_whisper import WhisperModel

    return WhisperModel(
        config.WHISPER_MODEL_SIZE,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE_TYPE,
    )


def _has_audio_stream(video_path: str) -> bool:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", video_path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    return bool(out.strip())


def transcribe(video_path: str) -> list[Word]:
    """Returns a flat, time-ordered list of words for the whole video.

    Videos with no audio track (e.g. a muted screen recording) are valid
    input, not an error — faster-whisper's underlying PyAV decoder raises a
    bare `IndexError: tuple index out of range` when asked to decode an audio
    stream that doesn't exist, so we check first rather than let that surface.
    """
    if not _has_audio_stream(video_path):
        return []

    try:
        segments, _info = _model().transcribe(video_path, word_timestamps=True)
    except Exception:
        # Degrade gracefully — see intent-pipeline skill: no extraction
        # sub-step should crash the whole run. An empty transcript is valid.
        return []

    words: list[Word] = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            words.append(Word(text=w.word.strip(), start=w.start, end=w.end))
    return words
