"""Stage 4: EDL -> ffmpeg render. Deterministic, no LLM involvement."""
import subprocess
import tempfile
from pathlib import Path

from app.schemas import EDL

_FADE_SEC = 0.03


def render(video_path: str, edl: EDL, output_path: str) -> None:
    if not edl.clips:
        raise ValueError("EDL has no clips — nothing to render.")

    with tempfile.TemporaryDirectory() as tmp:
        part_paths = []
        for i, clip in enumerate(edl.clips):
            part = Path(tmp) / f"part_{i:04d}.mp4"
            duration = clip.end - clip.start
            cmd = [
                "ffmpeg", "-y", "-ss", str(clip.start), "-i", video_path,
                "-t", str(duration),
                "-af", f"afade=t=in:st=0:d={_FADE_SEC},afade=t=out:st={max(duration - _FADE_SEC, 0)}:d={_FADE_SEC}",
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
