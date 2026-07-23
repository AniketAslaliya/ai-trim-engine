"""Silence detection via ffmpeg's silencedetect filter — no LLM, no ML model."""
import re
import subprocess

from app import config


def detect_silence(video_path: str) -> list[tuple[float, float]]:
    """Returns [(start, end), ...] silence intervals in seconds."""
    cmd = [
        "ffmpeg",
        "-i", video_path,
        "-af", f"silencedetect=noise={config.SILENCE_NOISE_DB}dB:d={config.SILENCE_MIN_DURATION_SEC}",
        "-f", "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stderr = proc.stderr

    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", stderr)]

    # ffmpeg logs silence_end even if silence runs to EOF is truncated; pair up what we can.
    intervals = list(zip(starts, ends))
    if len(starts) > len(ends):
        # trailing silence with no explicit end logged — leave it unpaired, extraction
        # pipeline will clip it to duration_sec.
        pass
    return intervals
