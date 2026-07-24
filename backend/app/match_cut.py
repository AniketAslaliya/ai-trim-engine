"""Real frame- and audio-level match-cut analysis at a specific cross-video
cut boundary.

This analyzes the ACTUAL two frames at the join (not a pre-extracted tag
proxy) via one multi-image LLM call, plus a real ffmpeg-measured audio
level delta across the same boundary. Still not full computer-vision
motion/optical-flow matching (see PRD §9a/§5b) — there is no tracked-object
trajectory or pixel-motion-vector comparison here, just a multimodal model's
holistic visual judgment on the two boundary frames. Honest about degrading
to an unscored result rather than fabricating a number when a frame can't
be extracted or the LLM call fails.
"""
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app import config, llm

_VISUAL_PROMPT = (
    "Image 1 is the LAST frame before a cut. Image 2 is the FIRST frame after the cut, from a "
    "DIFFERENT video. Judge how well this would work as a \"match cut\" — a cut where visual "
    "composition, subject position/pose, motion direction, or framing continues naturally "
    "across the join, so the cut feels intentional rather than jarring. Score 0-10 (10 = "
    "seamless match, 0 = jarring/unrelated) and give a one-sentence reason."
)

_VISUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "minimum": 0, "maximum": 10},
        "reason": {"type": "string"},
    },
    "required": ["score", "reason"],
}


def _extract_frame(video_path: str, at_sec: float) -> Optional[bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "frame.jpg"
        cmd = [
            "ffmpeg", "-y", "-ss", str(max(at_sec, 0)), "-i", video_path,
            "-frames:v", "1", "-q:v", "2", str(out),
        ]
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0 or not out.exists():
            return None
        return out.read_bytes()


def _mean_volume_db(video_path: str, start: float, duration: float) -> Optional[float]:
    """Real measured mean audio level (dB) over a short window via ffmpeg's
    volumedetect filter — checks whether a cut jumps abruptly in loudness or
    continues smoothly, an actual signal rather than a guess."""
    cmd = [
        "ffmpeg", "-ss", str(max(start, 0)), "-t", str(duration), "-i", video_path,
        "-af", "volumedetect", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    match = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", proc.stderr)
    return float(match.group(1)) if match else None


def score_join(video_a: str, a_end: float, video_b: str, b_start: float) -> dict:
    """Real analysis of one cross-video cut boundary. Returns None fields for
    whatever couldn't be measured rather than fabricating a plausible-looking
    number — an honest "unavailable" beats a fake "8/10"."""
    result: dict = {"visual_score": None, "visual_reason": None, "audio_delta_db": None, "natural": None}

    frame_a = _extract_frame(video_a, a_end)
    frame_b = _extract_frame(video_b, b_start)
    if frame_a and frame_b and config.llm_configured():
        try:
            data = llm.complete_multi_vision_json([frame_a, frame_b], _VISUAL_PROMPT, _VISUAL_SCHEMA, max_tokens=200)
            result["visual_score"] = data.get("score")
            result["visual_reason"] = data.get("reason")
        except Exception:
            pass  # degrade gracefully — see intent-pipeline skill

    vol_a = _mean_volume_db(video_a, max(a_end - 0.4, 0), 0.4)
    vol_b = _mean_volume_db(video_b, b_start, 0.4)
    if vol_a is not None and vol_b is not None:
        result["audio_delta_db"] = round(abs(vol_a - vol_b), 1)

    if result["visual_score"] is not None and result["audio_delta_db"] is not None:
        result["natural"] = result["visual_score"] >= 6 and result["audio_delta_db"] <= 6

    return result
