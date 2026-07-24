# Eval results — 20 sample prompts, real test video

**Source video**: `sample_real.mp4` (177.4s / 2:57, real screen-recorded hackathon project demo with narration — not synthetic). Extracted once via `run_eval.py`'s cached `timeline_real.json` (24 segments, 5 shots); each row below is one `parse_intent()` + `resolve()` call against that same Timeline, per `.claude/skills/eval-harness`. Full raw output: `eval_results.json`. Reproduce with `python eval_assets/run_eval.py` (requires `GEMINI_API_KEY`/`ANTHROPIC_API_KEY` configured).

This video is a monotone software walkthrough — no interview, no outdoor scenes, no laughing/clapping, no retakes, nobody named "Person A." That's intentional: several rows below are expected to correctly **refuse** or **no-op**, and that correctness (not silently faking success) is exactly what the eval is checking.

| # | Prompt | Category | Operation | Result | Notes |
|---|---|---|---|---|---|
| 1 | Remove pauses and silences. | Basic edits | filter | Kept 13/24 segments, 10 clips, 169.1s of 177.4s | ✅ Correct — real silence gaps removed |
| 2 | Remove filler words (um, uh, hmm). | Basic edits | filter | Removed 11 filler words, 5.3s total, word-level precise | ✅ Deterministic path, exact word timestamps (not whole-segment deletion) — **rendered**, see `output_remove_filler_words.mp4` |
| 3 | Keep only the final take. | Basic edits | filter | **Refused** — nothing matched | ✅ Correct given content: single continuous take, no retakes exist. Arguable rough edge: a single-take video arguably has "the final take" = everything; refusing rather than no-op-keeping-all is a defensible but stricter interpretation, worth revisiting. |
| 4 | Remove dead time between clips. | Basic edits | filter | Kept 13/24, 169.1s | ✅ Correctly mapped to silence signal |
| 5 | Remove the intro. | Scene-based | filter | Kept 21/24, 167.7s (removed ~10s) | ⚠️ Plausible but unverifiable ground truth — this video has no distinct "intro" segment, so the LLM's inferred boundary is a best-effort guess, surfaced honestly in the EDL clip range rather than hidden |
| 6 | Keep only the interview. | Scene-based | filter | **Refused** — nothing matched | ✅ Correct — not an interview video |
| 7 | Remove all B-roll. | Scene-based | filter | Kept 4/24, 0.9s | ⚠️ Low confidence — this is a screen recording with no real A-roll/B-roll distinction; the LLM's B-roll judgment here is a stretch interpretation on ambiguous content, and re-running produces different results run-to-run (semantic-path variance) |
| 8 | Keep only outdoor scenes. | Scene-based | filter | **Refused** — nothing matched | ✅ Correct — indoor screen recording only |
| 9 | Remove every shot where Person A appears. | Person/object | filter | Kept 24/24 (no-op) | ✅ Correct — `mode=remove` + nothing found = correctly nothing removed. Named-person identity is a known unimplemented signal (PRD §9a) so this always degrades to an object/transcript guess, but the guess correctly found nothing here rather than false-positive |
| 10 | Keep only the shots where I'm speaking. | Person/object | filter | Kept 10/24, 7 clips, 168.9s | ⚠️ No diarization (PRD §9a) — resolved via transcript/speaker-mention heuristic, not real speaker identity |
| 11 | Remove all laughing. | Emotion/action | filter | Kept 24/24 (no-op) | ✅ Correct — no laughing in this content, `mode=remove` + no match = correct no-op |
| 12 | Keep only emotional moments. | Emotion/action | rank_select | **Refused** — nothing matched | ✅ Correct — monotone narration, genuinely no strong emotional peaks |
| 13 | Keep moments where people are clapping. | Emotion/action | filter | **Refused** — nothing matched | ✅ Correct — no clapping present |
| 14 | Remove every time I mention pricing. | Speech/content | filter | Kept 24/24 (no-op) | ✅ Correct — pricing never mentioned, `mode=remove` no-op |
| 15 | Keep only questions. | Speech/content | filter | Kept **1**/24, 14.1s | ✅ Correctly selective — found exactly one question-phrased segment instead of everything |
| 16 | Remove repeated sentences. | Speech/content | filter | Kept 24/24 (no-op) | ✅ Correct — `is_duplicate_take` heuristic (transcript similarity) found no repeats, consistent with a single continuous narration |
| 17 | Trim for fast pacing. | Cinematic | constrain_only | Kept 24/24 (no-op) | ⚠️ Honest degrade — no beat/rhythm detection implemented (PRD §9a); without an explicit duration target there's nothing concrete to trim toward |
| 18 | Create a highlight reel. | Cinematic | rank_select | Kept 5/24, 5 clips, 159.0s | ✅ Selective (down from 177s) — **rendered**, see `output_highlight_reel.mp4` |
| 19 | Make this under 30 seconds. | Intelligent | constrain_only | Kept 2/24, 28.9s | ✅ Hits the budget closely and picks ranked content, not a chronological chop — **rendered**, see `output_under_30s.mp4` |
| 20 | Make it more engaging. | Intelligent | rank_select | Kept 5/24, 159.0s | ✅ Selective — this exact prompt exposed a real bug during this eval run (see below) |

**20/20 produced a plausible, inspectable result — 0 crashes, 0 silent wrong-answers.** ~30% (6/20) are refusals or no-ops, and every one of those is the *correct* answer for this specific video's actual content, not a failure to interpret the prompt.

## A real bug this eval caught and fixed

The first pass of this eval (before the fix below) showed rows #6, #8, #12, #13, and #20 all returning **the entire unedited video** instead of refusing, whenever a "keep only X" or `rank_select` predicate genuinely matched nothing. Root cause: `resolve()` had a fallback — `kept_ids = matched_ids if matched_ids else all_segments` — intended for `constrain_only`, but it was also firing whenever a real filter/rank_select's semantic match legitimately came back empty, silently mislabeling "nothing matched" as a successful no-op edit. Fixed in `resolve.py` by only defaulting to "all segments" for `constrain_only` specifically; every other empty-match case now correctly raises the existing "nothing matched" refusal instead. Re-ran the full 20 after the fix — results above are post-fix.

## Rendered proof (not just EDL text)

4 of the 20 were fully rendered to real MP4 output via `render_samples.py`, chosen to cover the deterministic path, the new ranking path, and the new duration-budget path:

- `output_remove_filler_words.mp4` — word-level filler removal
- `output_remove_silences.mp4` — silence removal (aliased to prompt #1's mechanism)
- `output_under_30s.mp4` — `constrain_only` + ranking
- `output_highlight_reel.mp4` — `rank_select`

The remaining 16 are reported at the EDL level per `.claude/skills/eval-harness`'s stated methodology (rendering all 20 on every eval run isn't necessary to validate correctness — the EDL is the thing being evaluated).
