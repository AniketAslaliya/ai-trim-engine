# Backend — AI Trim Engine

FastAPI service implementing the four-stage pipeline from [../PRD.md](../PRD.md) and
`.claude/skills/intent-pipeline/SKILL.md`. Schemas live in `app/schemas.py`, mirroring
`.claude/skills/timeline-schema/SKILL.md` exactly.

## Local setup

Requires `ffmpeg` on PATH (extraction and render both shell out to it).

```
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env        # fill in ANTHROPIC_API_KEY
uvicorn app.main:app --reload
```

## API

- `POST /videos` — multipart upload (`file`), kicks off extraction in the background. Returns `{video_id, job_id}`.
- `GET /videos/{video_id}/timeline` — the extracted Timeline JSON, once extraction's job is `done`.
- `POST /videos/{video_id}/edit?prompt=...` — kicks off intent parse → resolve → render in the background. Returns `{job_id}`.
- `GET /jobs/{job_id}` — status, and once done: the Intent, EDL, and output path.
- `GET /jobs/{job_id}/output` — the rendered MP4.
- `GET /health` — liveness check.

Poll `GET /jobs/{job_id}` until `status == "done"` (or `"failed"`, check `error`) — every heavy step runs as a background task, never inline in the request, so no endpoint here blocks for more than a request round-trip.

## Notes on this scaffold

- Job store is in-memory (`app/jobs/store.py`) — fine for a single-process day-one deploy; swap for Redis/DB before scaling past one worker.
- LLM calls go through `app/llm.py`, a thin provider-agnostic wrapper — `LLM_PROVIDER=gemini` (default, free tier) or `LLM_PROVIDER=anthropic`. Pipeline code never imports a provider SDK directly, so switching providers is a `.env` change, not a code change.
- Visual tagging (`app/extraction/visual_tags.py`) and semantic predicate resolution (`app/resolve/resolve.py: _resolve_semantic`) both call `app/llm.py`; without an API key configured for the selected provider they degrade to empty results rather than crashing — the mechanical prompt group (silence, filler words) still works with zero API key.
- Deterministic resolution only recognizes a few keyword patterns today (`is_silence`, `filler word(s)`, `speaker`) — extend `_DETERMINISTIC_KEYWORDS` in `resolve.py` as more signals get added to the Timeline, per the "adding a new capability" section of the intent-pipeline skill.
