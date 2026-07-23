# Frontend — AI Trim Engine

Minimal Next.js UI: upload a video, describe the edit in plain language, watch the result.
No auth, no state library — a single client component (`app/page.tsx`) polling the backend's
async job endpoints (see [../backend/README.md](../backend/README.md)).

## Local setup

```
npm install
copy .env.local.example .env.local   # points at the local backend by default
npm run dev
```

Requires the backend running (`../backend`, default `http://127.0.0.1:8000`) with CORS allowing
`http://localhost:3000` (already the backend's default — see `CORS_ORIGINS` in `backend/.env`).

## Flow

1. Upload a video → backend starts extraction (transcription, scene/silence detection) in the background; UI polls until done.
2. Type or pick a sample prompt → backend parses intent, resolves it against the extracted Timeline, renders the EDL; UI polls until done.
3. Parsed Intent, EDL summary, and the rendered result all display once the edit job finishes; errors surface directly rather than hanging silently.

`lib/api.ts` is the only place that talks to the backend — its types mirror `backend/app/schemas.py` (and `.claude/skills/timeline-schema/SKILL.md`). Keep them in sync if the backend schema changes.
