"""FastAPI app. Async job model throughout: extraction and edit requests both
enqueue a background task and return a job_id immediately — never render or
transcribe synchronously inside a request (see CLAUDE.md deployment note)."""
import json
import shutil
import uuid

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import config
from app.extraction.pipeline import build_timeline
from app.intent.parse import parse_intent
from app.jobs.store import create_job, get_job, update_job
from app.render.ffmpeg_render import render
from app.resolve.resolve import resolve
from app.schemas import Timeline

app = FastAPI(title="AI Trim Engine")


def _timeline_path(video_id: str) -> str:
    return str(config.video_dir(video_id) / "timeline.json")


def _video_path(video_id: str) -> str:
    matches = list(config.video_dir(video_id).glob("source.*"))
    if not matches:
        raise HTTPException(404, "source video not found for this video_id")
    return str(matches[0])


@app.post("/videos")
async def upload_video(file: UploadFile, background_tasks: BackgroundTasks):
    video_id = str(uuid.uuid4())
    suffix = "." + (file.filename.rsplit(".", 1)[-1] if "." in file.filename else "mp4")
    dest = config.video_dir(video_id) / f"source{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    job = create_job(video_id, kind="extraction")
    background_tasks.add_task(_run_extraction, job.job_id, video_id, str(dest))
    return {"video_id": video_id, "job_id": job.job_id}


def _run_extraction(job_id: str, video_id: str, video_path: str) -> None:
    update_job(job_id, status="running")
    try:
        timeline = build_timeline(video_id, video_path)
        with open(_timeline_path(video_id), "w") as f:
            f.write(timeline.model_dump_json(indent=2))
        update_job(job_id, status="done")
    except Exception as e:
        update_job(job_id, status="failed", error=str(e))


@app.get("/videos/{video_id}/timeline")
async def get_timeline(video_id: str):
    try:
        with open(_timeline_path(video_id)) as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(404, "timeline not ready — check extraction job status")


@app.post("/videos/{video_id}/edit")
async def edit_video(video_id: str, prompt: str, background_tasks: BackgroundTasks):
    try:
        with open(_timeline_path(video_id)) as f:
            timeline = Timeline(**json.load(f))
    except FileNotFoundError:
        raise HTTPException(404, "timeline not ready — run extraction first")

    job = create_job(video_id, kind="edit")
    background_tasks.add_task(_run_edit, job.job_id, video_id, timeline, prompt)
    return {"job_id": job.job_id}


def _run_edit(job_id: str, video_id: str, timeline: Timeline, prompt: str) -> None:
    update_job(job_id, status="running")
    try:
        intent = parse_intent(prompt)
        update_job(job_id, intent=intent)

        edl = resolve(intent, timeline)
        update_job(job_id, edl=edl)

        output_path = str(config.renders_dir(video_id) / f"{job_id}.mp4")
        render(_video_path(video_id), edl, output_path)

        update_job(job_id, status="done", output_path=output_path)
    except Exception as e:
        update_job(job_id, status="failed", error=str(e))


@app.get("/jobs/{job_id}")
async def job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.get("/jobs/{job_id}/output")
async def job_output(job_id: str):
    job = get_job(job_id)
    if job is None or not job.output_path:
        raise HTTPException(404, "no output for this job")
    return FileResponse(job.output_path, media_type="video/mp4")


@app.get("/health")
async def health():
    return {"status": "ok"}
