"""Stage 4: EDL -> ffmpeg render. Deterministic, no LLM involvement."""
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from app.schemas import EDL

_FADE_SEC = 0.008  # click-guard only, not an audible fade — 30ms on both sides
# of every internal join stacked into a noticeable dip; this is short enough
# to kill the waveform-discontinuity pop but too short to perceive as fading,
# so a cut reads as a clean join, not a stutter

# Common platform targets. "9:16" covers Reels/TikTok/Shorts requests.
_ASPECT_RATIOS = {
    "9:16": (9, 16),
    "16:9": (16, 9),
    "1:1": (1, 1),
    "4:5": (4, 5),
}


def _get_resolution(video_path: str) -> Optional[tuple[int, int]]:
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", video_path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    if "x" not in out:
        return None
    w, h = out.split("x")
    return int(w), int(h)


def _crop_filter(src_w: int, src_h: int, aspect_ratio: str) -> Optional[str]:
    """Centered crop-to-aspect-ratio, computed from the source's actual
    resolution rather than a dynamic ffmpeg expression — easier to test and
    reason about. Returns None for an unrecognized ratio string (caller
    should fall through to no reformatting rather than fail the render)."""
    ratio = _ASPECT_RATIOS.get(aspect_ratio)
    if ratio is None:
        return None
    target = ratio[0] / ratio[1]
    src_ratio = src_w / src_h
    if src_ratio > target:
        new_w = int(src_h * target) & ~1  # even width, required by most codecs
        x = (src_w - new_w) // 2
        return f"crop={new_w}:{src_h}:{x}:0"
    else:
        new_h = int(src_w / target) & ~1
        y = (src_h - new_h) // 2
        return f"crop={src_w}:{new_h}:0:{y}"


def render(video_path: str, edl: EDL, output_path: str, aspect_ratio: Optional[str] = None) -> None:
    if not edl.clips:
        raise ValueError("EDL has no clips — nothing to render.")

    vf = None
    if aspect_ratio:
        res = _get_resolution(video_path)
        if res:
            vf = _crop_filter(res[0], res[1], aspect_ratio)
        # An unrecognized ratio or unreadable resolution degrades to no
        # reformatting rather than failing the whole render — a wrong-shape
        # video is still useful; a failed render is not.

    with tempfile.TemporaryDirectory() as tmp:
        part_paths = []
        for i, clip in enumerate(edl.clips):
            part = Path(tmp) / f"part_{i:04d}.mp4"
            duration = clip.end - clip.start
            cmd = [
                "ffmpeg", "-y", "-ss", str(clip.start), "-i", video_path,
                "-t", str(duration),
                "-af", f"afade=t=in:st=0:d={_FADE_SEC},afade=t=out:st={max(duration - _FADE_SEC, 0)}:d={_FADE_SEC}",
            ]
            if vf:
                cmd += ["-vf", vf]
            cmd += [
                "-c:v", "libx264", "-c:a", "aac", "-avoid_negative_ts", "make_zero",
                str(part),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed on clip {i}: {proc.stderr[-500:]}")
            part_paths.append(part)

        concat_list = Path(tmp) / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in part_paths))

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy", output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {proc.stderr[-500:]}")


# Fixed canvas presets for aspect-ratio requests during compose — predictable
# output size regardless of what any individual source video's resolution is.
_CANVAS_FOR_RATIO = {
    "9:16": (720, 1280),
    "16:9": (1280, 720),
    "1:1": (1080, 1080),
    "4:5": (864, 1080),
}
_TARGET_FPS = 30


def render_multi(video_paths: dict[str, str], edl: EDL, output_path: str, aspect_ratio: Optional[str] = None) -> None:
    """Same idea as render(), but each clip can come from a DIFFERENT source
    file (see compose.py) — the real difference is that source videos can
    disagree on resolution/fps/audio format, and ffmpeg's concat demuxer
    requires every part to match exactly for the final -c copy step to work.
    Every clip gets scaled/padded (or cropped, if an aspect ratio was
    requested) to one common canvas and a common fps/audio format first.
    """
    if not edl.clips:
        raise ValueError("EDL has no clips — nothing to render.")

    if aspect_ratio and aspect_ratio in _CANVAS_FOR_RATIO:
        canvas_w, canvas_h = _CANVAS_FOR_RATIO[aspect_ratio]
        fit_mode = "fill"  # crop to fill the requested shape, matches single-video render's crop behavior
    else:
        first_path = video_paths[edl.clips[0].video_id]
        canvas_w, canvas_h = _get_resolution(first_path) or (1280, 720)
        fit_mode = "pad"  # preserve full frame by letterboxing, since no reformat was actually requested

    if fit_mode == "fill":
        scale_vf = f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,crop={canvas_w}:{canvas_h}"
    else:
        scale_vf = (
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,"
            f"pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:color=black"
        )
    vf = f"{scale_vf},setsar=1,fps={_TARGET_FPS}"

    with tempfile.TemporaryDirectory() as tmp:
        part_paths = []
        for i, clip in enumerate(edl.clips):
            if clip.video_id not in video_paths:
                raise ValueError(f"Clip {i} references video_id {clip.video_id!r}, which wasn't provided.")
            part = Path(tmp) / f"part_{i:04d}.mp4"
            duration = clip.end - clip.start
            cmd = [
                "ffmpeg", "-y", "-ss", str(clip.start), "-i", video_paths[clip.video_id],
                "-t", str(duration),
                "-af", (
                    f"afade=t=in:st=0:d={_FADE_SEC},"
                    f"afade=t=out:st={max(duration - _FADE_SEC, 0)}:d={_FADE_SEC}"
                ),
                "-vf", vf,
                "-r", str(_TARGET_FPS),
                "-c:v", "libx264", "-c:a", "aac", "-ar", "44100", "-ac", "2",
                "-avoid_negative_ts", "make_zero",
                str(part),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed on clip {i} (video_id={clip.video_id}): {proc.stderr[-500:]}")
            part_paths.append(part)

        concat_list = Path(tmp) / "concat.txt"
        concat_list.write_text("\n".join(f"file '{p.as_posix()}'" for p in part_paths))

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy", output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {proc.stderr[-500:]}")
