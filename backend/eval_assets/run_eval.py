"""Runs the eval-harness skill's 20 prompts against the cached real-video
Timeline (eval_assets/timeline_real.json) and writes a results table.
Deliberately does NOT re-run extraction — that's already cached — so this
is just Intent-parse + resolve per prompt, per .claude/skills/eval-harness.
"""
import json
import sys
import time
import traceback

sys.path.insert(0, ".")

from app.intent.parse import parse_intent
from app.resolve.resolve import resolve
from app.schemas import Timeline

PROMPTS = [
    "Remove pauses and silences.",
    "Remove filler words (um, uh, hmm).",
    "Keep only the final take.",
    "Remove dead time between clips.",
    "Remove the intro.",
    "Keep only the interview.",
    "Remove all B-roll.",
    "Keep only outdoor scenes.",
    "Remove every shot where Person A appears.",
    "Keep only the shots where I'm speaking.",
    "Remove all laughing.",
    "Keep only emotional moments.",
    "Keep moments where people are clapping.",
    "Remove every time I mention pricing.",
    "Keep only questions.",
    "Remove repeated sentences.",
    "Trim for fast pacing.",
    "Create a highlight reel.",
    "Make this under 30 seconds.",
    "Make it more engaging.",
]

with open("eval_assets/timeline_real.json") as f:
    timeline = Timeline(**json.load(f))

results = []
for i, prompt in enumerate(PROMPTS, 1):
    row = {"n": i, "prompt": prompt}
    t0 = time.time()
    try:
        intent = parse_intent(prompt)
        row["operation"] = intent.operation
        row["predicate"] = intent.predicate
        row["target_signal"] = intent.target_signal
        try:
            edl = resolve(intent, timeline)
            row["result"] = edl.summary
            row["clips"] = len(edl.clips)
            row["ok"] = True
        except ValueError as e:
            row["result"] = f"(refused) {e}"
            row["clips"] = 0
            row["ok"] = True  # a deliberate, explained refusal is correct behavior, not a failure
    except Exception as e:
        row["result"] = f"ERROR: {e}"
        row["ok"] = False
        print(f"--- FULL TRACEBACK for prompt {i} ---")
        traceback.print_exc()
    row["time_s"] = round(time.time() - t0, 1)
    results.append(row)
    print(f"[{i:2d}/20] ({row['time_s']:5.1f}s) {prompt}")
    print(f"        -> {row.get('operation', '?')} | {row['result'][:140]}")

with open("eval_assets/eval_results.json", "w") as f:
    json.dump(results, f, indent=2)

print("\nSaved eval_assets/eval_results.json")
print(f"OK: {sum(r['ok'] for r in results)}/{len(results)}")
