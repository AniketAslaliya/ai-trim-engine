"""Stage 2: the ONLY place raw user language is interpreted. Everything
downstream operates on the structured Intent this produces, never on the
original prompt string again (see intent-pipeline skill, Stage 2)."""
from app import llm
from app.schemas import Intent

_SYSTEM = """You convert a natural-language video-editing request into a structured Intent.

operation meanings — pick the one that actually matches the request, don't default to filter:
- "filter": a clear-cut keep/remove condition (silence, a topic, a visible object, a person).
- "rank_select": "best/funniest/most important/highlight" style requests — selecting a
  SUBSET by quality/relevance, not a hard yes/no condition. Order the result by rank.
- "reorder": the request wants segments re-sequenced, not just trimmed — "build a story",
  "start with the strongest hook", "3-act structure", match-cut/trailer-style requests.
- "constrain_only": a length/format target ("under 30 seconds", "make it shorter", "for
  Reels") with no other explicit selection criterion — let a later stage pick the best
  content to fit the budget.

target_signal is the set of Timeline fields this predicate can be checked against. Available
fields: transcript, scene_tags, objects, action_tags (visible actions/expressions like
laughing, clapping, walking, close_up — from a visual check, not audio), audio_events,
is_silence, filler_words, is_duplicate_take (a segment whose content closely repeats in a
later segment — i.e. an earlier retake), speaker. Use is_duplicate_take for "remove
retakes"/"keep only the final take"-style requests. Use action_tags for visible
laughing/clapping/walking-style requests — there is no audio emotion/laughter detector,
only visual evidence, so don't invent a signal that isn't in this list.

Rules:
- Never invent a 5th operation. Compose filter/rank_select/reorder/constrain_only.
- Vague requests ("make it shorter", "more engaging") must still resolve to a concrete,
  committed predicate — do not leave ambiguity for a later stage.
- "predicate" is free text resolved later against a per-segment Timeline; make it
  concrete and checkable (e.g. "segments where the transcript mentions pricing or cost"),
  not vague restatement of the prompt.
- Only set constraints fields the user actually implied (duration targets, platform/aspect ratio,
  background noise cleanup).
- Set constraints.denoise_audio = true whenever the request mentions cleaning up background
  noise, hiss, hum, wind noise, static, or otherwise wants the audio "cleaner"/"clearer" — this
  is independent of operation/predicate; a request can be PURELY "remove the background noise"
  (operation="constrain_only", predicate can note there's no cutting to do) or combined with a
  cutting request in the same prompt (e.g. "remove the silences and clean up the audio noise").
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
                "denoise_audio": {"type": "boolean"},
            },
            "required": ["max_duration_sec", "min_segment_gap_sec", "aspect_ratio", "denoise_audio"],
        },
    },
    "required": ["operation", "mode", "predicate", "target_signal", "constraints"],
}


def parse_intent(prompt: str) -> Intent:
    data = llm.complete_json(_SYSTEM, prompt, _INTENT_SCHEMA, max_tokens=400)
    return Intent(**data)
