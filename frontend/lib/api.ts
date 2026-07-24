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
  video_id: string | null;
  segment_ids: number[];
  start: number;
  end: number;
}

export interface Transition {
  at_clip_boundary: number;
  type: "audio_fade" | "cut";
  duration_sec: number;
  // Only set for cross-video joins in a compose sequence — real frame/audio
  // analysis from match_cut.py, not a tag-similarity guess.
  visual_score: number | null;
  visual_reason: string | null;
  audio_delta_db: number | null;
}

export interface EDL {
  video_id: string;
  clips: Clip[];
  transitions: Transition[];
  summary: string;
}

export interface JobStatus {
  job_id: string;
  video_id: string;
  kind: "extraction" | "edit" | "compose";
  status: "pending" | "running" | "done" | "failed";
  progress: string | null;
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

export async function retryExtraction(videoId: string): Promise<{ job_id: string }> {
  const resp = await fetch(`${API_BASE}/videos/${videoId}/retry-extraction`, { method: "POST" });
  return asJson(resp);
}

/** Manual trim: a user-drawn timeline selection, cut with no LLM call at all
 * (pure time-range math on the backend) — instant and free compared to a
 * chat prompt going through intent parsing. */
export async function manualEdit(
  videoId: string,
  removeRanges: { start: number; end: number }[],
  denoiseAudio = false
): Promise<{ job_id: string }> {
  const resp = await fetch(`${API_BASE}/videos/${videoId}/manual-edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ remove_ranges: removeRanges, denoise_audio: denoiseAudio }),
  });
  return asJson(resp);
}

export async function getTimeline(videoId: string): Promise<Timeline> {
  const resp = await fetch(`${API_BASE}/videos/${videoId}/timeline`);
  return asJson(resp);
}

/** Same as getTimeline, but returns null instead of throwing if the timeline
 * isn't written yet (used while polling mid-extraction — the file doesn't
 * exist for the first moment before the first progress callback fires). */
export async function getTimelineIfReady(videoId: string): Promise<Timeline | null> {
  const resp = await fetch(`${API_BASE}/videos/${videoId}/timeline`);
  if (resp.status === 404) return null;
  return asJson(resp);
}

export interface KeepRange {
  start: number;
  end: number;
}

/** The current cumulative cut state (source-time ranges still kept after
 * every edit applied so far) — lets the timeline render the actual current
 * sequence instead of the untouched original layout. */
export async function getKeepRanges(videoId: string): Promise<KeepRange[]> {
  const resp = await fetch(`${API_BASE}/videos/${videoId}/keep-ranges`);
  const data = await asJson<{ keep_ranges: KeepRange[] }>(resp);
  return data.keep_ranges;
}

/** Combine several already-extracted videos into one sequence per a
 * natural-language description of the story/order — see backend/app/compose.py. */
export async function composeVideos(videoIds: string[], prompt: string): Promise<{ job_id: string }> {
  const resp = await fetch(`${API_BASE}/compose`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_ids: videoIds, prompt }),
  });
  return asJson(resp);
}

/** No-LLM re-render of a manually reordered/trimmed/deleted compose timeline
 * — the frontend sends the full final clip list, the backend just re-runs
 * real match_cut scoring on the joins and renders. See backend/app/compose.py
 * build_manual_compose_edl. */
export async function composeManualEdit(
  videoIds: string[],
  clips: { video_id: string; start: number; end: number }[],
  aspectRatio?: string | null,
  denoiseAudio = false
): Promise<{ job_id: string }> {
  const resp = await fetch(`${API_BASE}/compose/manual`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ video_ids: videoIds, clips, aspect_ratio: aspectRatio ?? null, denoise_audio: denoiseAudio }),
  });
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
