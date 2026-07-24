"""Stage 2: the ONLY place raw user language is interpreted. Everything
downstream operates on the structured Intent this produces, never on the
original prompt string again (see intent-pipeline skill, Stage 2)."""
from app import llm
from app.schemas import Intent

_SYSTEM = """You convert a natural-language video-editing request into a structured Intent.

Rules:
- Never invent a 5th operation. Compose filter/rank_select/reorder/constrain_only.
- Vague requests ("make it shorter", "more engaging") must still resolve to a concrete,
  committed predicate — do not leave ambiguity for a later stage.
- "predicate" is free text resolved later against a per-segment Timeline; make it
  concrete and checkable (e.g. "segments where the transcript mentions pricing or cost"),
  not vague restatement of the prompt.
- Only set constraints fields the user actually implied (duration targets, platform/aspect ratio).
"""

# Hand-written, not Intent.model_json_schema() — Pydantic v2's auto-generated
# schema uses keywords (default, allOf, $ref-with-siblings) outside the subset
# Gemini's response_json_schema actually supports, which silently produces a
# rejected or mis-shaped schema. Keep this in sync with app/schemas.py:Intent.
_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "operation": {"type": "string", "enum": ["filter", "rank_select", "reorder", "constrain_only"]},
        "mode": {"type": "string", "enum": ["keep", "remove"]},
        "predicate": {"type": "string"},
        "target_signal": {"type": "array", "items": {"type": "string"}},
        "constraints": {
            "type": "object",
            "properties": {
                "max_duration_sec": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "min_segment_gap_sec": {"type": "number"},
                "aspect_ratio": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["max_duration_sec", "min_segment_gap_sec", "aspect_ratio"],
        },
    },
    "required": ["operation", "mode", "predicate", "target_signal", "constraints"],
}


def parse_intent(prompt: str) -> Intent:
    data = llm.complete_json(_SYSTEM, prompt, _INTENT_SCHEMA, max_tokens=400)
    return Intent(**data)
