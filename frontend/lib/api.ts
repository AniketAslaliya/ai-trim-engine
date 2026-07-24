// Thin client for the FastAPI backend. Mirrors backend/app/schemas.py — keep
// field names in sync with that file (and .claude/skills/timeline-schema).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export interface Constraints {
  max_duration_sec: number | null;
  min_segment_gap_sec: number;
  aspect_ratio: string | null;
}

export interface Intent {
  operation: "filter" | "rank_select" | "reorder" | "constrain_only";
  mode: "keep" | "remove";
  predicate: string;
  target_signal: string[];
  constraints: Constraints;
}

export interface Clip {
  segment_ids: number[];
  start: number;
  end: number;
}

export interface EDL {
  video_id: string;
  clips: Clip[];
  summary: string;
}

export interface JobStatus {
  job_id: string;
  video_id: string;
  kind: "extraction" | "edit";
  status: "pending" | "running" | "done" | "failed";
  error: string | null;
  intent: Intent | null;
  edl: EDL | null;
  output_path: string | null;
}

export interface Segment {
  id: number;
  start: number;
  end: number;
  transcript: string;
  is_silence: boolean;
  shot_boundary: boolean;
  scene_tags: string[];
  objects: string[];
  filler_words: unknown[];
}

export interface Timeline {
  video_id: string;
  duration_sec: number;
  segments: Segment[];
}

async function asJson<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`${resp.status} ${resp.statusText}: ${body}`);
  }
  return resp.json();
}

export async function uploadVideo(file: File): Promise<{ video_id: string; job_id: string }> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch(`${API_BASE}/videos`, { method: "POST", body: form });
  return asJson(resp);
}

export async function getJob(jobId: string): Promise<JobStatus> {
  const resp = await fetch(`${API_BASE}/jobs/${jobId}`);
  return asJson(resp);
}

export async function submitEdit(videoId: string, prompt: string): Promise<{ job_id: string }> {
  const resp = await fetch(
    `${API_BASE}/videos/${videoId}/edit?prompt=${encodeURIComponent(prompt)}`,
    { method: "POST" }
  );
  return asJson(resp);
}

export async function getTimeline(videoId: string): Promise<Timeline> {
  const resp = await fetch(`${API_BASE}/videos/${videoId}/timeline`);
  return asJson(resp);
}

export function outputUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/output`;
}

/** Polls a job until it reaches "done" or "failed", calling onUpdate on each poll. */
export async function pollJob(
  jobId: string,
  onUpdate: (job: JobStatus) => void,
  intervalMs = 2000
): Promise<JobStatus> {
  while (true) {
    const job = await getJob(jobId);
    onUpdate(job);
    if (job.status === "done" || job.status === "failed") return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
