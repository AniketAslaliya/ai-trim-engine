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

| Group | Examples | Primitive(s) | Signal(s) needed | Status |
|---|---|---|---|---|
| Basic edits | remove pauses, filler words, retakes, dead time, keep final take | filter | word timestamps, silence detection, transcript-similarity retake clustering | ✅ implemented (silence/filler deterministic + word-level; retakes via `is_duplicate_take` heuristic) |
| Scene-based | remove intro/outro, keep only interview, remove B-roll, keep outdoor scenes | filter | shot boundaries + scene/location tags (VLM caption per shot) | ✅ scene_tags/objects work well; "B-roll"/"interview" rely on the LLM inferring it from tags, no explicit shot-role classifier |
| Person/object | remove Person A's shots, keep shots where product visible | filter | face/person clustering, object tags per shot | ⚠️ object-based (laptop, phone, product) works; named-person identity (`Person A`) and speaker-based ("I'm speaking") are **not implemented** — see §9a |
| Emotion/action | remove awkward moments, keep funniest, keep clapping | filter + rank_select | visual `action_tags` (laughing/clapping/walking from the shot keyframe), LLM ranking | ⚠️ visual-evidence only, no audio emotion detector — see §9a |
| Speech/content | remove pricing mentions, keep only questions, remove off-topic | filter | transcript + LLM classification per segment | ✅ implemented via semantic resolve |
| Cinematic | fast pacing, match cuts, cut on beat, trailer-style | rank_select + reorder | LLM ranking + LLM ordering plan | ⚠️ ranking/reordering implemented; true beat/rhythm-synced cutting is **not** (no audio-rhythm analysis) |
| Storytelling | best moments, hook first, payoff last, story arc | rank_select + reorder | LLM ranking + LLM ordering plan | ✅ implemented — `reorder` builds clips in the LLM's given sequence instead of always chronological |
| Intelligent/vague | "make it shorter," "more engaging," "under 30s," "focus on me" | constrain_only (+ implicit rank) | `constrain_only` now ranks by the intent's own predicate before trimming to the duration budget, instead of chopping chronologically | ✅ implemented |

**Format/platform requests** ("make suitable for Reels/TikTok") set `constraints.aspect_ratio`, which the render stage now actually acts on — a centered crop to the target ratio (9:16, 1:1, 16:9, 4:5), computed from the source's real resolution, applied during the per-clip encode pass. Previously this constraint was captured by intent parsing but silently ignored by rendering.

**Background noise removal** ("clean up the background noise," "remove the hiss/hum") sets `constraints.denoise_audio`, recognized by the same intent parser alongside (or independent of) a cutting request. Applied at render time as an FFT noise-reduction pass (ffmpeg's `afftdn` filter — no external model file, unlike `arnndn`) on every clip's audio before the fade guards. Real signal processing, not a placeholder — verified end-to-end against real footage (valid output, audio stream intact). Also exposed as a one-click "Remove background noise" button on the single-video timeline (no LLM call needed, same instant/free path as manual trim) and via chat/compose prompts on both the single-video editor and the combine flow. Like `aspect_ratio`, it is not persisted cumulatively — each edit's own prompt determines whether denoise is applied to that render.

### 5b. Multi-video composition ("phase 2" — combine + match cut)

A second capability alongside single-video trimming: `POST /compose` takes several already-extracted `video_id`s plus one natural-language description of the desired sequence/story, and produces one combined output pulling clips from multiple source files.

- **Sequencing**: one LLM call (`compose.py`) sees EVERY segment from every provided video — including silent ones, each tagged `is_silence` rather than pre-filtered out — tagged with its source `video_id`, and returns the segments to use in final output order. This reuses the same tag data (`transcript`/`scene_tags`/`objects`/`action_tags`) the single-video pipeline already extracts — no new extraction work. The prompt explicitly instructs the LLM to only exclude segments the instruction actually asked to remove; a silent segment is real visual content (e.g. a silent hand-gesture shot) and must not be dropped just because it has no dialogue. (This used to be hardcoded — `is_silence` segments were unconditionally excluded from candidates regardless of what the user asked, which meant compose could silently "remove silence" the user never requested. Fixed.)
- **Match cuts — real frame + audio analysis, with a genuine boundary search, not a tag proxy** (`match_cut.py`): once a sequence is chosen, every cross-video join is re-examined for real. `find_best_trim` searches nearby candidate cut points INSIDE each side's own segment bounds (a coordinate-descent search, up to 2×`steps` real vision-LLM calls per join, never `steps²`) — at each candidate, the actual two boundary frames are extracted with ffmpeg and sent to a vision LLM in one call (`llm.complete_multi_vision_json`) to score visual continuity (composition/pose/framing, 0–10 + one-line reason); the best-scoring pair of cut points is what actually gets used for the clip boundaries, not just the segment's original edge. This is what lets a preparatory/wind-up motion at a segment's tail or head get trimmed away automatically when a later/earlier point in the SAME segment scores meaningfully better — genuine "cut on the matching moment," not a fixed boundary. The search never extends outside the segment that was already selected — it only refines WHERE inside chosen content to cut, never WHAT content is included. Separately, `ffmpeg`'s `volumedetect` filter measures the real mean audio level either side of the chosen boundary, and the two levels' delta (dB) is reported. All numbers are genuine measurements on real boundary frames/audio — verified end-to-end against real footage. This is still **not** full frame-level CV match-cut detection: no optical flow, no tracked-object trajectory, no motion-vector comparison — it's a multimodal model's holistic judgment on real frame pairs, sampled at a handful of candidate points, not a continuous motion analysis. If frame extraction or the LLM call fails, the join degrades to the tag-overlap count rather than fabricating a score. See §9a.
- **Cross-source rendering** (`render_multi` in `ffmpeg_render.py`): different source videos can disagree on resolution/fps/audio format, which the single-video pipeline never had to handle. Every clip is normalized (scaled+letterboxed to the first source's resolution by default, or cropped-to-fill a requested aspect ratio) to a common canvas/fps/audio format before concatenation, verified against real mixed-resolution-risk test videos.
- **Contiguous-segment merging** (`_merge_contiguous`): a chosen sequence often includes several consecutive segments from the same source video (extraction splits on sentence/silence boundaries, not story beats). These are now collapsed into one clip before rendering — the single-video pipeline already did this (`resolve.py`'s `_segments_to_clips`); compose_sequence didn't, which is what produced extra unwanted internal cuts/fades in a span that should have been one continuous shot. A cross-video boundary is never merged, so this can't interfere with a match cut. Verified end-to-end on a real 3-video compose: went from a fragmented multi-clip-per-video result to exactly one clip per source video plus the genuine cross-video match-cut boundaries.
- **Storage/job model**: unchanged async job pattern (`kind="compose"`), stored under `storage/_compose/` since a composed output doesn't belong to any single source video.

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

- **ASR errors** → wrong cut boundaries for word-level operations. Mitigation: pad cuts by ~100-150ms (see `_FILLER_PAD_SEC`), never cut mid-word.
- **Visual tagging false negatives/positives** (e.g., "laptop visible") → wrong segments removed. Mitigation: report confidence, skip segments below a threshold rather than guessing.
- **Ambiguous boundaries** ("remove the intro") → LLM must infer where intro ends; no ground truth. Mitigation: surface the inferred boundary in the edit summary so it's checkable.
- **Subjective prompts** ("more engaging," "funniest") → no ground truth, high run-to-run variance. Mitigation: `rank_select` treats these as an explainable ordering (see §5a), not a black-box decision; default to selecting roughly half of what's ranked-relevant when no explicit duration is given, rather than "keep everything vaguely related."
- **Conflicting/impossible prompts** ("under 30s" but nothing else to cut) → mitigation: return the closest achievable result plus a clear message, never silently fail or overcut past a floor.
- **Empty result** (predicate matches nothing, or everything) → mitigation: refuse and explain rather than emitting a 0-length or unedited video.

### 9a. Known capability gaps (honestly unimplemented, not silently faked)

These are real limitations, not bugs — each is a deliberate scope call given day-build constraints, not something the system will falsely claim to do:

- **Speaker diarization** — `Segment.speaker` exists in the schema but is never populated (no diarization model wired in). "Keep only the shots where I'm speaking" cannot be resolved by speaker identity today; it degrades to a semantic guess over transcript/action_tags (e.g. "talking_to_camera") instead.
- **Named person identification / on-screen tracking** — "remove every shot where Person A appears," "remove everything before I enter the frame" need face detection + identity clustering across the video. Not implemented — would need a real CV pipeline (face embeddings + clustering), out of scope for the current build. The system will not fabricate a person-identity signal; these prompts fall through to a best-effort transcript/object guess.
- **True audio emotion/event detection** (laughter, clapping, applause as *audio*) — not implemented; there is no audio classifier. What *is* implemented: `action_tags` from the per-shot visual keyframe (the same VLM call as `scene_tags`/`objects`) can genuinely detect visible laughing/clapping/walking when it's visually evident in the frame — real signal, but visual-only, and will miss audio-only laughter (someone laughing off-camera or the camera not catching an expressive frame at the right instant).
- **Beat/rhythm detection** ("cut on every beat") — not implemented; no audio-rhythm analysis (e.g. onset/beat detection). Cinematic pacing requests degrade to `rank_select`/`reorder` on transcript+visual signals, not actual beat-synced cutting.
- **Match cuts use real frame/audio analysis with a bounded boundary search, but still not true CV matching** — see §5b. `match_cut.find_best_trim` extracts real candidate boundary frames at a handful of points inside each side's own segment and sends them to a vision LLM for a real continuity judgment, plus measures a real ffmpeg audio-level delta at the chosen point — genuine analysis of the real footage, not a tag proxy, and the actual cut point is adjusted to the best-scoring candidate rather than fixed at the segment's original edge. It is still not frame-level *CV* match-cut detection: no optical flow, no pose/skeleton tracking, no motion-vector comparison, and the search only samples a handful of points (not a continuous scan) — it can miss a better cut point between samples, or misjudge subtle motion-direction/composition matches a dedicated CV pipeline (or a human editor) would catch. Falls back to the cheaper tag-overlap count if frame extraction or the LLM call fails, rather than fabricating a score.
- **Retake detection is heuristic, not exact** — `is_duplicate_take` (see §5a below) is transcript-similarity based (difflib ratio > 0.6 against a *later* segment). It will miss retakes with substantially different wording and could rarely false-positive on genuinely repeated phrases. Documented in `extraction/retakes.py`.

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
- Intent/reasoning LLM: provider-agnostic (`backend/app/llm.py`) — Gemini free tier (`gemini-2.5-flash`) by default for day-one cost reasons, Claude API as a documented fallback via `LLM_PROVIDER=anthropic`. Structured JSON output either way.
- Backend: FastAPI + ffmpeg, containerized, deployed on Render
- Frontend: Next.js on Vercel (UI only — no heavy compute in serverless functions)
- Job model: async — enqueue render job, poll/websocket for status (avoids serverless/HTTP timeouts)
