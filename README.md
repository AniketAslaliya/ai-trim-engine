# AI Trim Engine

Natural-language video trimming: describe an edit in plain English, get a rendered cut. See [`PRD.md`](PRD.md) for architecture and design decisions, and [`backend/eval_assets/EVAL_RESULTS.md`](backend/eval_assets/EVAL_RESULTS.md) for sample outputs across 20 prompts run against a real test video.

**Live demo**: https://ai-trim-engine.vercel.app (backend on Render's free tier — see note below)

## Quickstart (run locally with your own API key)

### Prerequisites

- Python 3.11+
- Node.js 20+
- [ffmpeg](https://ffmpeg.org/download.html) on your `PATH` (`ffmpeg -version` should work)
- A Gemini API key ([aistudio.google.com](https://aistudio.google.com/apikey)) or an Anthropic API key — either works, see below

### 1. Clone

```
git clone https://github.com/AniketAslaliya/ai-trim-engine.git
cd ai-trim-engine
```

### 2. Backend

```
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows — use `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

Edit `backend/.env` and set your key:

```
LLM_PROVIDER=gemini
GEMINI_API_KEY=<your key>
GEMINI_MODEL=gemini-flash-lite-latest
```

(To use Claude instead: `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=<your key>`.)

Run it:

```
uvicorn app.main:app --reload
```

Backend is now at `http://127.0.0.1:8000` — confirm with `curl http://127.0.0.1:8000/health`.

### 3. Frontend

In a second terminal:

```
cd frontend
npm install
copy .env.local.example .env.local     # macOS/Linux: cp .env.local.example .env.local
npm run dev
```

Frontend is now at `http://localhost:3000`, already configured to talk to your local backend.

### 4. Try it

Open `http://localhost:3000`, upload a short video (or use `backend/eval_assets/sample_real.mp4`, the real video this project's eval was run against), and try a prompt — or one of the 20 in [`EVAL_RESULTS.md`](backend/eval_assets/EVAL_RESULTS.md).

## Reproducing the eval

```
cd backend
python eval_assets/run_eval.py       # re-parses+resolves all 20 prompts against the cached real-video Timeline
python eval_assets/render_samples.py # renders 4 of them to actual MP4s
```

## Known limitation: hosted demo storage

The live demo's backend runs on Render's **free tier**, which has no persistent disk — any redeploy or 15-minute idle timeout wipes uploaded videos entirely (not just job status). If the live demo shows a "job/video not found" error, just re-upload — this doesn't happen when running locally.
