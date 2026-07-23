import os
from pathlib import Path

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "./storage")).resolve()
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

SILENCE_NOISE_DB = float(os.environ.get("SILENCE_NOISE_DB", "-30"))
SILENCE_MIN_DURATION_SEC = float(os.environ.get("SILENCE_MIN_DURATION_SEC", "0.4"))

FILLER_WORDS = {"um", "umm", "uh", "uhh", "hmm", "like", "erm", "you know"}

STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def video_dir(video_id: str) -> Path:
    d = STORAGE_DIR / video_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def renders_dir(video_id: str) -> Path:
    d = video_dir(video_id) / "renders"
    d.mkdir(parents=True, exist_ok=True)
    return d
