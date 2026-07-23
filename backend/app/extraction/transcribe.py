"""Word-level transcription via faster-whisper. Runs once per video, cached by the caller."""
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


def transcribe(video_path: str) -> list[Word]:
    """Returns a flat, time-ordered list of words for the whole video."""
    segments, _info = _model().transcribe(video_path, word_timestamps=True)
    words: list[Word] = []
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            words.append(Word(text=w.word.strip(), start=w.start, end=w.end))
    return words
