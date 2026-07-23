# AI Trim Engine — project instructions

Full spec: [PRD.md](PRD.md). Read it once per session if unsure of scope — don't re-derive architecture from scratch.

## What this is

A natural-language video trimming engine. Pipeline: extract structured **Timeline** from video (deterministic) → LLM parses prompt into **Intent** → Intent resolved against Timeline into **EDL** (Edit Decision List) → ffmpeg renders EDL. Full pipeline detail and schemas: `.claude/skills/intent-pipeline/SKILL.md` and `.claude/skills/timeline-schema/SKILL.md` — load those before writing pipeline code instead of re-deriving the schema inline.

## Core principle — do not violate

The LLM reasons over **text** (transcript + tags + timestamps), never raw frames or video bytes. If a change would make the LLM consume raw video/frames directly, stop and reconsider — that breaks the token-efficiency design and is almost never necessary.

## Repo layout (target)

```
backend/        FastAPI app: extraction stage, intent stage, resolution stage, render stage
  extraction/   whisper, silence detect, scene detect, VLM captioning, diarization
  intent/       prompt -> Intent JSON (LLM call)
  resolve/      Intent + Timeline -> EDL
  render/       EDL -> ffmpeg render
  schemas.py    Timeline / Intent / EDL pydantic models — single source of truth
frontend/       Next.js UI (upload, prompt box, job status, result player) — no heavy compute here
.claude/skills/ pipeline + schema + eval reference material (see below)
```

## Conventions

- New "prompt types" must be handled by extending the **predicate/ranking logic**, not by adding a new hardcoded function per prompt. If you catch yourself writing `if "laughing" in prompt: ...`, stop — route it through the general filter/rank primitive instead (see PRD §5).
- Every render must go through an explicit EDL artifact — never let the LLM call ffmpeg directly.
- Keep extraction-stage outputs cached per video (don't re-run Whisper/scene-detect per prompt).
- No fine-tuned models, no dense per-frame captioning — sample at shot boundaries only (day-one scope, PRD §3).

## Deployment targets

Frontend → Vercel (UI only). Backend (ffmpeg/Whisper/render) → Render, containerized. Async job model — never render synchronously inside an HTTP request.

## Skills

- `.claude/skills/timeline-schema/` — canonical Timeline/Intent/EDL JSON schemas. Load before touching any pipeline stage.
- `.claude/skills/intent-pipeline/` — step-by-step guidance for implementing/extending a pipeline stage.
- `.claude/skills/eval-harness/` — the 20 sample prompts used to validate the engine, and how to run/report an eval pass.
