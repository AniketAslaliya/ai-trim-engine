"""Renders a handful of representative eval prompts to real output files —
proof-of-output for the sample-outputs deliverable, not just EDL summaries."""
import json
import sys

sys.path.insert(0, ".")

from app.intent.parse import parse_intent
from app.render.ffmpeg_render import render
from app.resolve.resolve import resolve
from app.schemas import Timeline

SOURCE = "eval_assets/sample_real.mp4"

with open("eval_assets/timeline_real.json") as f:
    timeline = Timeline(**json.load(f))

SAMPLES = [
    ("remove_filler_words", "Remove filler words (um, uh, hmm)."),
    ("remove_silences", "Remove pauses and silences."),
    ("under_30s", "Make this under 30 seconds."),
    ("highlight_reel", "Create a highlight reel."),
]

for name, prompt in SAMPLES:
    print(f"=== {name}: {prompt!r} ===")
    intent = parse_intent(prompt)
    edl = resolve(intent, timeline)
    print("  ", edl.summary)
    out_path = f"eval_assets/output_{name}.mp4"
    render(SOURCE, edl, out_path, aspect_ratio=intent.constraints.aspect_ratio)
    print("  rendered ->", out_path)
