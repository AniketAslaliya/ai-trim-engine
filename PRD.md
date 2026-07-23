# PRD — Personalized AI Trim Engine

## 1. Problem

Editors spend most of their time on mechanical trimming (silences, filler words, retakes, dead time) and semantic selection (find-and-cut a scene, a person, a topic) before any creative editing starts. Existing "auto-trim" tools use fixed rules for a fixed list of operations. This project builds an engine that takes a natural-language instruction and produces the corresponding edit, including instructions it has never explicitly been coded for.

## 2. Goal

A prompt-to-edit engine: `(source video, natural language instruction) → trimmed video`, built on a general intent → filter/rank → EDL → render pipeline, not a switch-statement of hardcoded features.

## 3. Non-goals (v1 / one-day build)

- No generative video (no inpainting, no B-roll synthesis, no AI-generated cutaways).
- No fine-tuned highlight-detection model — "best moments" ranking is done via LLM reasoning over transcript + tags, not a trained saliency model.
- No dense per-frame visual analysis — visual understanding is sampled at shot boundaries, not every frame.
- No real-time/streaming editing.
- No Premiere/Adobe integration in v1 (documented as a future extension — MCP servers for Premiere already exist and could sit in front of the same EDL).

## 4. Users

- Solo creators / vloggers with raw footage who want a "just cut the boring parts" pass.
- Reviewer of this assignment, who needs to see: architecture reasoning, generalization beyond the example list, and honest edge-case handling.

## 5. Functional requirements

All items below reduce to three primitives — **filter** (keep/remove segments matching a condition), **rank-select** (choose the best N segments by a scored criterion), **constrain** (fit duration/aspect ratio/pacing target). Requirement groups map to these primitives so new prompts don't need new code paths, only better predicates/signals.

| Group | Examples | Primitive(s) | Signal(s) needed |
|---|---|---|---|
| Basic edits | remove pauses, filler words, retakes, dead time, keep final take | filter | word timestamps, silence detection, repeated-transcript detection |
| Scene-based | remove intro/outro, keep only interview, remove B-roll, keep outdoor scenes | filter | shot boundaries + scene/location tags (VLM caption per shot) |
| Person/object | remove Person A's shots, keep shots where product visible | filter | face/person clustering, object tags per shot |
| Emotion/action | remove awkward moments, keep funniest, keep clapping | filter + rank | audio/visual emotion tags, laughter/clap audio events, LLM ranking |
| Speech/content | remove pricing mentions, keep only questions, remove off-topic | filter | transcript + LLM classification per segment |
| Cinematic | fast pacing, match cuts, cut on beat, trailer-style | constrain + rank | beat/rhythm detection, LLM pacing plan |
| Storytelling | best moments, hook first, payoff last, story arc | rank + reorder | LLM ranking + LLM ordering plan |
| Intelligent/vague | "make it shorter," "more engaging," "under 30s," "focus on me" | constrain (+ implicit filter/rank) | LLM resolves vague request into one of the above primitives with explicit constraints |

## 6. Architecture (see CLAUDE.md + `.claude/skills/*` for implementation-level detail)

```
source video
   │
   ▼
[Extraction stage — deterministic, no LLM]
  Whisper (word timestamps) · silence detect (ffmpeg) · scene/shot detect (PySceneDetect)
  · sparse VLM captions per shot · speaker diarization · beat/audio-event detection
   │
   ▼
Timeline (single JSON: ordered segments with transcript/speaker/tags/silence flags)
   │
   ▼
[Intent stage — LLM, structured output]
  prompt → {operation: filter|rank|constrain, predicate, constraints}
   │
   ▼
[Resolution stage — LLM applied over Timeline, or deterministic code for simple predicates]
  predicate → concrete segment id list to keep
   │
   ▼
Edit Decision List (EDL): ordered [start,end] ranges + transition specs
   │
   ▼
[Render stage — deterministic, ffmpeg]
  concat/trim + audio fades at cut boundaries
   │
   ▼
[Self-check — cheap, optional]
  duration/coverage assertions, optional single VLM pass on composited filmstrip
   │
   ▼
output video + human-readable edit summary
```

## 7. Non-functional requirements

- **Token efficiency**: LLM only ever reasons over the text Timeline (KB-scale), never raw frames/video. Sparse VLM captioning is cached per shot, called once.
- **Debuggability**: Intent JSON and EDL are both persisted artifacts a human can inspect before render — no direct "LLM calls ffmpeg" step.
- **Cost**: target ≤2 LLM calls per user request (intent parse + predicate resolution) for simple prompts; ranking/storytelling prompts may need one extra call.
- **Latency**: extraction stage runs once per uploaded video and is cached; only intent+resolution+render run per prompt.

## 8. Success metrics (for this assignment)

- 20/20 sample prompts produce a plausible, inspectable EDL (not necessarily a "correct" render for subjective ones).
- At least the mechanical/objective prompt group (basic edits, scene-based, person/object, speech/content) renders correctly on a real test video.
- Documented failure modes for the subjective/visual-guesswork group, with a stated mitigation (confirmation step, confidence threshold, fallback to no-op with explanation).

## 9. Known risks / edge cases

- **ASR errors** → wrong cut boundaries for word-level operations. Mitigation: pad cuts by ~100-150ms, never cut mid-word.
- **Visual tagging false negatives/positives** (e.g., "laptop visible") → wrong segments removed. Mitigation: report confidence, skip segments below a threshold rather than guessing.
- **Ambiguous boundaries** ("remove the intro") → LLM must infer where intro ends; no ground truth. Mitigation: surface the inferred boundary in the edit summary so it's checkable.
- **Subjective prompts** ("more engaging," "funniest") → no ground truth, high run-to-run variance. Mitigation: treat as ranking with an explainable score, not a black-box decision; state this limitation explicitly rather than overclaiming accuracy.
- **Conflicting/impossible prompts** ("under 30s" but nothing else to cut) → mitigation: return the closest achievable result plus a clear message, never silently fail or overcut past a floor.
- **Empty result** (predicate matches nothing, or everything) → mitigation: refuse and explain rather than emitting a 0-length or unedited video.

## 10. Deliverables checklist (per assignment)

- [ ] Working prototype (backend pipeline + minimal UI)
- [ ] Architecture doc (this PRD + `.claude/skills/intent-pipeline/SKILL.md`)
- [ ] Prompt → edit pipeline (implemented + demonstrated)
- [ ] Edge cases & failure handling (§9, backed by actual test runs)
- [ ] 20 sample prompts with outputs (rendered where feasible, EDL-level otherwise)

## 11. Stack decisions (for the one-day build)

- Transcription: `faster-whisper` (local, free, word timestamps)
- Scene detection: PySceneDetect (`detect-content`)
- Visual tagging: CLIP zero-shot or a cheap VLM call per shot keyframe
- Intent/reasoning LLM: Claude API (structured JSON output)
- Backend: FastAPI + ffmpeg, containerized, deployed on Render
- Frontend: Next.js on Vercel (UI only — no heavy compute in serverless functions)
- Job model: async — enqueue render job, poll/websocket for status (avoids serverless/HTTP timeouts)
