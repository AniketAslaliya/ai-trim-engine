"""Manual smoke test: exercises extraction -> resolve -> render directly
(bypassing the FastAPI/job-queue layer) against the synthetic test clip.
Deletable once we have a real automated test suite.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extraction.pipeline import build_timeline
from app.render.ffmpeg_render import render
from app.resolve.resolve import resolve
from app.schemas import Constraints, Intent

VIDEO_PATH = str(Path(__file__).parent / "sample.mp4")
OUTPUT_PATH = str(Path(__file__).parent / "output_silence_removed.mp4")

print("=== Stage 1: extraction ===")
t0 = time.time()
timeline = build_timeline("smoke-test-video", VIDEO_PATH)
print(f"extraction took {time.time() - t0:.1f}s")
print(f"duration_sec={timeline.duration_sec:.2f}, segments={len(timeline.segments)}")
for s in timeline.segments:
    print(f"  id={s.id:2d} [{s.start:6.2f}-{s.end:6.2f}] is_silence={s.is_silence} "
          f"shot_boundary={s.shot_boundary} transcript={s.transcript!r}")

print("\n=== Stage 2: intent (manual, deterministic predicate) ===")
intent = Intent(
    operation="filter",
    mode="remove",
    predicate="segments where is_silence is true",
    target_signal=["is_silence"],
    constraints=Constraints(),
)
print(intent.model_dump_json(indent=2))

print("\n=== Stage 3: resolve ===")
edl = resolve(intent, timeline)
print(edl.model_dump_json(indent=2))

print("\n=== Stage 4: render ===")
t0 = time.time()
render(VIDEO_PATH, edl, OUTPUT_PATH)
print(f"render took {time.time() - t0:.1f}s -> {OUTPUT_PATH}")
