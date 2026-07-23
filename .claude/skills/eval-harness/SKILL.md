---
name: eval-harness
description: The 20 sample prompts used to validate the AI Trim Engine, plus how to run and report an eval pass. Load this when asked to "run the eval," "test the pipeline," "generate sample outputs," or when preparing the assignment's sample-outputs deliverable.
---

# Eval harness

## Sample prompt set (assignment deliverable — 20 prompts across all categories)

1. Remove pauses and silences.
2. Remove filler words (um, uh, hmm).
3. Keep only the final take.
4. Remove dead time between clips.
5. Remove the intro.
6. Keep only the interview.
7. Remove all B-roll.
8. Keep only outdoor scenes.
9. Remove every shot where Person A appears.
10. Keep only the shots where I'm speaking.
11. Remove all laughing.
12. Keep only emotional moments.
13. Keep moments where people are clapping.
14. Remove every time I mention pricing.
15. Keep only questions.
16. Remove repeated sentences.
17. Trim for fast pacing.
18. Create a highlight reel.
19. Make this under 30 seconds.
20. Make it more engaging.

Deliberately spans: mechanical/deterministic (1,2,4,16), scene-based (5,6,7,8), person/object (9,10), emotion/action (11,12,13), speech/content (14,15), cinematic/storytelling (17,18), vague/intelligent (19,20) — one from nearly every PRD §5 category, weighted toward the ones with objective ground truth so results are actually checkable.

## Running an eval pass

For each prompt, against one or more real test videos:

1. Run the full pipeline, capture the Intent JSON and EDL JSON (not just the final render) — these are the primary artifact, since rendering all 20 on every run is slow and the EDL is what's actually being evaluated.
2. Record: prompt → Intent.predicate → matched segment count / total → EDL.summary.
3. For the deterministic-signal group (1,2,4,9,10,14,15,16,19), check correctness against the Timeline directly (e.g. did it actually drop every `is_silence` segment) — this has a real pass/fail.
4. For the semantic/subjective group (5,6,7,8,11,12,13,17,18,20), report the result plus a one-line honest note on confidence — these don't have a ground truth, don't report them as pass/fail.
5. Only fully render (ffmpeg) a subset (3-4 prompts) as end-to-end proof; the rest can stay at the EDL-inspection level for the sample-outputs deliverable.

## Reporting format

A table: `# | Prompt | Category | Intent (operation/predicate) | Result (segments kept/removed, duration) | Rendered? | Notes/confidence`. This table itself satisfies the "sample outputs for 20 prompts" deliverable without requiring 20 full renders.
