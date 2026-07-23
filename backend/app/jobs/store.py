"""In-memory job store. Fine for a single-process day-one deployment; if this
needs to survive restarts or scale beyond one worker, swap for Redis/DB
without touching callers (they only see get/set/create)."""
import uuid

from app.schemas import JobStatus


_jobs: dict[str, JobStatus] = {}


def create_job(video_id: str, kind: str) -> JobStatus:
    job = JobStatus(job_id=str(uuid.uuid4()), video_id=video_id, kind=kind, status="pending")
    _jobs[job.job_id] = job
    return job


def get_job(job_id: str) -> JobStatus | None:
    return _jobs.get(job_id)


def update_job(job_id: str, **fields) -> None:
    job = _jobs[job_id]
    _jobs[job_id] = job.model_copy(update=fields)
