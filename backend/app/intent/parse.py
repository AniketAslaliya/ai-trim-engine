"""Stage 2: the ONLY place raw user language is interpreted. Everything
downstream operates on the structured Intent this produces, never on the
original prompt string again (see intent-pipeline skill, Stage 2)."""
import json

from app import llm
from app.schemas import Intent

_SYSTEM = """You convert a natural-language video-editing request into a structured Intent.

Intent JSON shape:
{
  "operation": "filter" | "rank_select" | "reorder" | "constrain_only",
  "mode": "keep" | "remove",
  "predicate": "<plain-English description of which segments this applies to>",
  "target_signal": ["transcript" | "scene_tags" | "objects" | "audio_events" | "is_silence" | "filler_words" | "speaker"],
  "constraints": {"max_duration_sec": number|null, "min_segment_gap_sec": number, "aspect_ratio": string|null}
}

Rules:
- Never invent a 5th operation. Compose filter/rank_select/reorder/constrain_only.
- Vague requests ("make it shorter", "more engaging") must still resolve to a concrete,
  committed predicate — do not leave ambiguity for a later stage.
- "predicate" is free text resolved later against a per-segment Timeline; make it
  concrete and checkable (e.g. "segments where the transcript mentions pricing or cost"),
  not vague restatement of the prompt.
- Only set constraints fields the user actually implied (duration targets, platform/aspect ratio).
- Respond with ONLY the JSON object, no commentary.
"""


def parse_intent(prompt: str) -> Intent:
    text = llm.complete_text(_SYSTEM, prompt, max_tokens=400)
    data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    return Intent(**data)
