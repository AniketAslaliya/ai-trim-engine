import os
from pathlib import Path

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "./storage")).resolve()
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "base")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")

# Provider-agnostic LLM config — pipeline code calls app.llm, never a provider SDK
# directly, so this is the only place a provider swap touches. "gemini" (free tier)
# is the default for the day-one build; "anthropic" is the documented fallback.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


def llm_configured() -> bool:
    return bool(GEMINI_API_KEY) if LLM_PROVIDER == "gemini" else bool(ANTHROPIC_API_KEY)


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
