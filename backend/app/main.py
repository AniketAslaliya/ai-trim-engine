"""FastAPI app. Async job model throughout: extraction and edit requests both
enqueue a background task and return a job_id immediately — never render or
transcribe synchronously inside a request (see CLAUDE.md deployment note)."""
import json
import shutil
import traceback
import uuid

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app import config
from app.compose import build_manual_compose_edl, compose_sequence
from app.edit_state import intersect_ranges, load_keep_ranges, ranges_to_edl, reset_keep_ranges, save_keep_ranges
from app.extraction.pipeline import build_timeline
from app.intent.parse import parse_intent
from app.jobs.store import create_job, get_job, update_job
from app.render.ffmpeg_render import render, render_multi
from app.resolve.manual import build_manual_edl
from app.resolve.resolve import resolve
from app.schemas import EDL, ComposeManualEditRequest, ComposeRequest, ManualEditRequest, Timeline

app = FastAPI(title="AI Trim Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.post("/videos/{video_id}/retry-extraction")
async def retry_extraction(video_id: str, background_tasks: BackgroundTasks):
    """Re-runs extraction on the already-uploaded source file — lets the UI
    offer a Retry button without re-uploading (e.g. after a transient/OOM
    failure once WHISPER_MODEL_SIZE or other config has been fixed)."""
    video_path = _video_path(video_id)
    job = create_job(video_id, kind="extraction")
    background_tasks.add_task(_run_extraction, job.job_id, video_id, video_path)
    return {"job_id": job.job_id}


def _compose_with_history(video_id: str, duration: float, one_shot_edl: EDL) -> EDL:
    """Intersects a freshly-resolved edit (computed against the pristine
    original Timeline, as if it were the only edit) with whatever's already
    been kept from prior edits on this video, and persists the result — this
    is what makes chat messages/manual trims actually stack instead of each
    one re-cutting from scratch."""
    new_ranges = [(c.start, c.end) for c in one_shot_edl.clips]
    current_ranges = load_keep_ranges(video_id, duration)
    composed = intersect_ranges(current_ranges, new_ranges)
    if not composed:
        raise ValueError(
            "This edit would remove everything remaining in the video after your previous "
            "edits. Try a less restrictive prompt, or upload a fresh copy to start over."
        )
    save_keep_ranges(video_id, composed)
    return ranges_to_edl(video_id, composed, one_shot_edl.summary)


def _run_extraction(job_id: str, video_id: str, video_path: str) -> None:
    update_job(job_id, status="running", progress="Starting...")

    def on_progress(msg: str, partial_timeline: Timeline) -> None:
        update_job(job_id, progress=msg)
        # Persisted immediately so GET /videos/{id}/timeline reflects
        # whatever's done so far, not just the final result — the frontend
        # can render segments as they're produced instead of a blank screen.
        with open(_timeline_path(video_id), "w") as f:
            f.write(partial_timeline.model_dump_json(indent=2))

    try:
        timeline = build_timeline(video_id, video_path, on_progress=on_progress)
        with open(_timeline_path(video_id), "w") as f:
            f.write(timeline.model_dump_json(indent=2))
        reset_keep_ranges(video_id)
        update_job(job_id, status="done", progress="Done.")
    except Exception as e:
        # Full traceback, not just str(e) — a bare exception message is enough
        # to diagnose a KeyError but useless for "tuple index out of range"
        # style errors that could originate several calls deep in a dependency.
        print(traceback.format_exc())
        update_job(job_id, status="failed", error=f"{e}\n\n{traceback.format_exc()}")


@app.get("/videos/{video_id}/timeline")
async def get_timeline(video_id: str):
    try:
        with open(_timeline_path(video_id)) as f:
            return json.load(f)
    except FileNotFoundError:
        raise HTTPException(404, "timeline not ready — check extraction job status")


@app.get("/videos/{video_id}/keep-ranges")
async def get_keep_ranges(video_id: str):
    """The current cumulative cut state (source-time [start,end] pairs still
    kept after every edit applied so far) — lets the UI render the timeline
    as the actual current sequence, not the untouched original layout."""
    try:
        with open(_timeline_path(video_id)) as f:
            duration = json.load(f)["duration_sec"]
    except FileNotFoundError:
        raise HTTPException(404, "timeline not ready — run extraction first")
    ranges = load_keep_ranges(video_id, duration)
    return {"keep_ranges": [{"start": s, "end": e} for s, e in ranges]}


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

        one_shot_edl = resolve(intent, timeline)
        edl = _compose_with_history(video_id, timeline.duration_sec, one_shot_edl)
        update_job(job_id, edl=edl)

        output_path = str(config.renders_dir(video_id) / f"{job_id}.mp4")
        render(
            _video_path(video_id), edl, output_path,
            aspect_ratio=intent.constraints.aspect_ratio, denoise=intent.constraints.denoise_audio,
        )

        update_job(job_id, status="done", output_path=output_path)
    except Exception as e:
        print(traceback.format_exc())
        update_job(job_id, status="failed", error=f"{e}\n\n{traceback.format_exc()}")


@app.post("/videos/{video_id}/manual-edit")
async def manual_edit_video(video_id: str, req: ManualEditRequest, background_tasks: BackgroundTasks):
    try:
        with open(_timeline_path(video_id)) as f:
            timeline = Timeline(**json.load(f))
    except FileNotFoundError:
        raise HTTPException(404, "timeline not ready — run extraction first")

    job = create_job(video_id, kind="edit")
    background_tasks.add_task(_run_manual_edit, job.job_id, video_id, timeline, req)
    return {"job_id": job.job_id}


def _run_manual_edit(job_id: str, video_id: str, timeline: Timeline, req: ManualEditRequest) -> None:
    update_job(job_id, status="running")
    try:
        one_shot_edl = build_manual_edl(timeline, req.remove_ranges)
        edl = _compose_with_history(video_id, timeline.duration_sec, one_shot_edl)
        update_job(job_id, edl=edl)

        output_path = str(config.renders_dir(video_id) / f"{job_id}.mp4")
        render(_video_path(video_id), edl, output_path, denoise=req.denoise_audio)

        update_job(job_id, status="done", output_path=output_path)
    except Exception as e:
        print(traceback.format_exc())
        update_job(job_id, status="failed", error=f"{e}\n\n{traceback.format_exc()}")


@app.post("/compose")
async def compose_videos(req: ComposeRequest, background_tasks: BackgroundTasks):
    """Combine several already-extracted videos into one sequence, per a
    natural-language description of the desired story/order (compose.py)."""
    if len(req.video_ids) < 2:
        raise HTTPException(400, "compose needs at least 2 video_ids")

    timelines: dict[str, Timeline] = {}
    for vid in req.video_ids:
        try:
            with open(_timeline_path(vid)) as f:
                timelines[vid] = Timeline(**json.load(f))
        except FileNotFoundError:
            raise HTTPException(404, f"timeline not ready for video_id {vid} — run extraction first")

    job = create_job(req.video_ids[0], kind="compose")
    background_tasks.add_task(_run_compose, job.job_id, req.video_ids, timelines, req.prompt)
    return {"job_id": job.job_id}


def _run_compose(job_id: str, video_ids: list[str], timelines: dict[str, Timeline], prompt: str) -> None:
    update_job(job_id, status="running", progress="Working out the sequence...")
    try:
        # Built up front (not just before render) so compose_sequence can run
        # real frame/audio analysis at each cross-video join, not just the
        # tag-similarity heuristic — see match_cut.py.
        video_paths = {vid: _video_path(vid) for vid in video_ids}

        edl = compose_sequence(prompt, timelines, video_paths)
        update_job(job_id, edl=edl, progress="Rendering...")

        # compose_sequence only resolves the sequence itself; format requests
        # ("...for Instagram Reels") live in the same free-text prompt, so
        # reuse the existing intent parser just to harvest that constraint —
        # same mechanism single-video edits already use, not new logic.
        aspect_ratio = None
        denoise = False
        try:
            constraints = parse_intent(prompt).constraints
            aspect_ratio = constraints.aspect_ratio
            denoise = constraints.denoise_audio
        except Exception:
            pass  # a failed/unparseable constraint hint is not worth failing the whole render over

        output_path = str(config.compose_dir() / f"{job_id}.mp4")
        render_multi(video_paths, edl, output_path, aspect_ratio=aspect_ratio, denoise=denoise)

        update_job(job_id, status="done", progress="Done.", output_path=output_path)
    except Exception as e:
        print(traceback.format_exc())
        update_job(job_id, status="failed", error=f"{e}\n\n{traceback.format_exc()}")


@app.post("/compose/manual")
async def compose_manual_edit(req: ComposeManualEditRequest, background_tasks: BackgroundTasks):
    """No-LLM re-render of a compose sequence the user has manually reordered/
    trimmed/deleted clips in via the Premiere-style compose timeline (see
    build_manual_compose_edl) — instant and free, same as single-video
    manual-edit, but for a cross-video sequence."""
    if not req.clips:
        raise HTTPException(400, "no clips provided")

    video_paths = {vid: _video_path(vid) for vid in req.video_ids}
    timelines: dict[str, Timeline] = {}
    for vid in req.video_ids:
        try:
            with open(_timeline_path(vid)) as f:
                timelines[vid] = Timeline(**json.load(f))
        except FileNotFoundError:
            pass  # widening the match-cut search is a bonus, not required — manual edit still works without it
    job = create_job(req.video_ids[0], kind="compose")
    background_tasks.add_task(
        _run_compose_manual, job.job_id, video_paths, timelines, req.clips, req.aspect_ratio, req.denoise_audio
    )
    return {"job_id": job.job_id}


def _run_compose_manual(
    job_id: str, video_paths: dict[str, str], timelines: dict[str, Timeline], clips_in, aspect_ratio: str | None, denoise: bool = False,
) -> None:
    update_job(job_id, status="running", progress="Applying manual edit...")
    try:
        clip_tuples = [(c.video_id, c.start, c.end) for c in clips_in]
        edl = build_manual_compose_edl(clip_tuples, video_paths, timelines)
        update_job(job_id, edl=edl, progress="Rendering...")

        output_path = str(config.compose_dir() / f"{job_id}.mp4")
        render_multi(video_paths, edl, output_path, aspect_ratio=aspect_ratio, denoise=denoise)

        update_job(job_id, status="done", progress="Done.", output_path=output_path)
    except Exception as e:
        print(traceback.format_exc())
        update_job(job_id, status="failed", error=f"{e}\n\n{traceback.format_exc()}")


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
