---
name: intent-pipeline
description: How to implement or extend a stage of the trim engine pipeline (extraction, intent-parsing, resolution, render). Load this before writing pipeline code, adding a new "supported prompt," or debugging why a prompt produced a wrong edit — it defines which stage owns which logic and the rule for adding new capabilities without hardcoding per-prompt functions.
---

# Implementing the trim pipeline

Four stages, always in this order: **Extraction → Intent → Resolution → Render**. Schemas for the artifacts passed between them: `.claude/skills/timeline-schema/SKILL.md` (load that too).

## Stage 1 — Extraction (deterministic, run once per video, cached)

Produces the Timeline. Sub-steps, each independent and individually testable:

1. Transcription: `faster-whisper`, word-level timestamps.
2. Silence detection: ffmpeg `silencedetect`, mark segments `is_silence`.
3. Shot/scene detection: PySceneDetect `detect-content`, defines segment boundaries.
4. Speaker diarization: pyannote (optional — skip if single-speaker source, don't block on it).
5. Visual tagging: one VLM/CLIP call per shot keyframe → `scene_tags` + `objects`. Never per-frame.
6. Audio events (laughter/clapping/music): only if a cheap detector is available; leave `audio_events: []` rather than guessing.

Rule: every sub-step must degrade gracefully (missing diarization ≠ pipeline failure, just `speaker: null` everywhere). Never let an extraction sub-step crash the whole run.

## Stage 2 — Intent parsing (one LLM call, structured output)

Prompt → Intent JSON (schema in timeline-schema skill). This is the *only* place the LLM sees raw user language ungrounded in the Timeline.

Rule for handling a prompt you haven't seen before: **do not add a new `operation` type or a new hardcoded branch.** Every prompt in the assignment's list (and any new one) decomposes into `filter` + `rank_select` + `reorder` + `constrain_only`, composed. If a prompt seems to need a 5th primitive, that's a sign the predicate/constraint fields need a new *field*, not the pipeline a new *stage*.

Vague prompts ("make it more engaging", "focus on me") should resolve to an Intent with an explicit predicate the LLM committed to (e.g. "focus on me" → `filter, mode=keep, predicate="segments where the primary speaker/subject is on camera"`) — never leave ambiguity for a later stage to silently interpret differently.

## Stage 3 — Resolution (Intent + Timeline → EDL)

Two paths depending on predicate complexity:

- **Deterministic path**: predicate maps directly to a Timeline field/condition (`is_silence == true`, `speaker == "SPEAKER_01"`) → resolve with plain code, no LLM call. Prefer this whenever possible — it's free, exact, and testable.
- **LLM path**: predicate requires semantic judgment over `transcript`/`scene_tags`/`objects` text (e.g. "coffee-making shots", "off-topic conversation", "funniest moments") → one LLM call over the Timeline (text only) that returns matching segment ids, or for `rank_select`, a scored/ordered list with justification per pick.

Always emit a human-readable `summary` in the EDL — this is what makes the "how sure are we" problem tractable: a reviewer (or the user) can read the EDL summary before render and catch a bad interpretation for free.

Enforce constraints here, not earlier: if `max_duration_sec` is set, after filtering, trim by dropping lowest-ranked/least-relevant segments until the target is met — never truncate arbitrarily from the end.

## Stage 4 — Render (deterministic, ffmpeg)

EDL clips → ffmpeg concat, with ~30ms audio fade at each cut boundary (avoids clicks/pops). No LLM involvement. If a clip list would render to near-zero duration or the full original duration unchanged, refuse and surface the EDL summary as the error message instead of rendering a degenerate result — see PRD §9 edge cases.

## Adding a new capability

1. Check if it's expressible as an existing primitive with a new predicate string — usually yes, add zero code.
2. If it needs a new *signal* (e.g. a new tag type from extraction), add it to the Timeline schema as an optional field and wire one extraction sub-step to populate it.
3. Only touch Stage 2/3 branching logic if the primitive itself is new (rare — filter/rank/reorder/constrain has covered every example in the PRD).
