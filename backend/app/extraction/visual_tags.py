"""Sparse visual tagging: one VLM call per shot keyframe, never per-frame.

Extracts a single mid-shot frame via ffmpeg and asks the configured LLM for
scene/object tags as JSON. Degrades to empty tags on any failure — extraction
must never crash because tagging failed (see intent-pipeline skill: "degrade
gracefully").
"""
import json
import subprocess
import tempfile
from pathlib import Path

from app import config, llm

_PROMPT = (
    "Look at this video frame. Return ONLY a JSON object with two arrays: "
    '"scene_tags" (location/setting descriptors, e.g. "office", "outdoor", "indoor") '
    'and "objects" (visible objects relevant to editing decisions, e.g. "laptop", "phone", "whiteboard", "product"). '
    "Keep each array short (max 5 items). No commentary, JSON only."
)


def _extract_keyframe(video_path: str, at_sec: float) -> bytes | None:
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


def tag_shot(video_path: str, shot_start: float, shot_end: float) -> tuple[list[str], list[str]]:
    """Returns (scene_tags, objects) for the shot, or ([], []) on any failure."""
    if not config.llm_configured():
        return [], []

    frame_bytes = _extract_keyframe(video_path, (shot_start + shot_end) / 2)
    if frame_bytes is None:
        return [], []

    try:
        text = llm.complete_vision(frame_bytes, _PROMPT, max_tokens=200)
        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
        return list(data.get("scene_tags", [])), list(data.get("objects", []))
    except Exception:
        # Never let a tagging failure take down extraction — empty tags are valid.
        return [], []
