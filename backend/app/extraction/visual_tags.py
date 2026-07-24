"""Sparse visual tagging: one VLM call per shot keyframe, never per-frame.

Extracts a single mid-shot frame via ffmpeg and asks the configured LLM for
scene/object/action tags as JSON. Degrades to empty tags on any failure —
extraction must never crash because tagging failed (see intent-pipeline
skill: "degrade gracefully").
"""
import subprocess
import tempfile
from pathlib import Path

from app import config, llm

_PROMPT = (
    "Look at this video frame. Identify: "
    '"scene_tags" (location/setting descriptors, e.g. "office", "outdoor", "indoor"), '
    '"objects" (visible objects relevant to editing decisions, e.g. "laptop", "phone", "whiteboard", "product"), '
    'and "action_tags" (visible actions/expressions/framing relevant to editing decisions — only include ones '
    'clearly visible in THIS frame, e.g. "laughing", "smiling", "clapping", "walking", "close_up", "talking_to_camera", '
    '"wide_shot" — do not guess ones that aren\'t visually evident). '
    "Keep each array short (max 5 items)."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "scene_tags": {"type": "array", "items": {"type": "string"}},
        "objects": {"type": "array", "items": {"type": "string"}},
        "action_tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["scene_tags", "objects", "action_tags"],
}


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


def tag_shot(video_path: str, shot_start: float, shot_end: float) -> tuple[list[str], list[str], list[str]]:
    """Returns (scene_tags, objects, action_tags) for the shot, or ([], [], []) on any failure."""
    if not config.llm_configured():
        return [], [], []

    frame_bytes = _extract_keyframe(video_path, (shot_start + shot_end) / 2)
    if frame_bytes is None:
        return [], [], []

    try:
        data = llm.complete_vision_json(frame_bytes, _PROMPT, _SCHEMA, max_tokens=250)
        return (
            list(data.get("scene_tags", [])),
            list(data.get("objects", [])),
            list(data.get("action_tags", [])),
        )
    except Exception:
        # Never let a tagging failure take down extraction — empty tags are valid.
        return [], [], []
