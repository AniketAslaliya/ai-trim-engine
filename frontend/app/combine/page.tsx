"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { composeVideos, outputUrl, pollJob, uploadVideo } from "@/lib/api";

type VideoStatus = "uploading" | "extracting" | "ready" | "failed";

interface UploadedVideo {
  video_id: string;
  name: string;
  status: VideoStatus;
  progress: string;
}

type ComposeStage = "idle" | "composing" | "done" | "failed";

export default function CombinePage() {
  const [videos, setVideos] = useState<UploadedVideo[]>([]);
  const [prompt, setPrompt] = useState("");
  const [stage, setStage] = useState<ComposeStage>("idle");
  const [progressText, setProgressText] = useState("");
  const [summary, setSummary] = useState<string | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const readyVideos = videos.filter((v) => v.status === "ready");
  const busy = videos.some((v) => v.status === "uploading" || v.status === "extracting") || stage === "composing";

  async function handleFilesChosen(files: FileList) {
    for (const file of Array.from(files)) {
      const entry: UploadedVideo = { video_id: "", name: file.name, status: "uploading", progress: "Uploading..." };
      setVideos((v) => [...v, entry]);
      try {
        const { video_id, job_id } = await uploadVideo(file);
        setVideos((v) => v.map((x) => (x === entry ? { ...x, video_id, status: "extracting", progress: "Extracting..." } : x)));
        const job = await pollJob(job_id, (j) => {
          setVideos((v) => v.map((x) => (x.video_id === video_id ? { ...x, progress: j.progress || j.status } : x)));
        });
        setVideos((v) =>
          v.map((x) =>
            x.video_id === video_id
              ? { ...x, status: job.status === "done" ? "ready" : "failed", progress: job.status === "done" ? "Ready" : job.error || "Extraction failed" }
              : x
          )
        );
      } catch (e) {
        setVideos((v) => v.map((x) => (x === entry ? { ...x, status: "failed", progress: String(e) } : x)));
      }
    }
  }

  async function handleCombine() {
    if (readyVideos.length < 2 || !prompt.trim()) return;
    setStage("composing");
    setError(null);
    setSummary(null);
    setResultUrl(null);
    setProgressText("Working out the sequence...");
    try {
      const { job_id } = await composeVideos(readyVideos.map((v) => v.video_id), prompt.trim());
      const job = await pollJob(job_id, (j) => setProgressText(j.progress || j.status));
      if (job.status === "failed") {
        setError(job.error || "Compose failed.");
        setStage("failed");
        return;
      }
      setSummary(job.edl?.summary || "Combined.");
      setResultUrl(outputUrl(job.job_id));
      setStage("done");
      setProgressText("");
    } catch (e) {
      setError(String(e));
      setStage("failed");
    }
  }

  return (
    <div className="flex h-screen flex-col bg-neutral-950 text-neutral-100">
      <header className="flex items-center justify-between border-b border-neutral-800 px-4 py-2.5">
        <div className="flex items-center gap-3">
          <Link href="/" className="text-xs text-neutral-500 hover:text-neutral-300">
            ← Back to editor
          </Link>
          <h1 className="text-sm font-semibold">Combine Videos</h1>
        </div>
      </header>

      {progressText && (
        <div className="flex items-center gap-2 border-b border-sky-900 bg-sky-950/60 px-4 py-2 text-xs text-sky-200">
          <span className="h-2 w-2 animate-pulse rounded-full bg-sky-400" />
          {progressText}
        </div>
      )}

      <main className="mx-auto w-full max-w-2xl flex-1 overflow-y-auto p-6">
        <p className="text-sm text-neutral-400">
          Upload 2 or more videos, then describe the sequence you want — the engine picks segments from each and
          combines them into one video, preferring visually-similar cut points across videos where it can (a
          tag-based match-cut heuristic, not true shot-matching CV).
        </p>

        <div className="mt-4">
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) handleFilesChosen(e.target.files);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="rounded-md bg-neutral-800 px-3 py-1.5 text-xs font-medium hover:bg-neutral-700"
          >
            + Add video(s)
          </button>
        </div>

        {videos.length > 0 && (
          <ul className="mt-3 space-y-2">
            {videos.map((v, i) => (
              <li key={i} className="flex items-center justify-between rounded-md border border-neutral-800 bg-neutral-950 px-3 py-2 text-sm">
                <span className="truncate text-neutral-200">{v.name}</span>
                <span
                  className={
                    v.status === "ready"
                      ? "text-xs text-emerald-400"
                      : v.status === "failed"
                      ? "text-xs text-red-400"
                      : "text-xs text-neutral-500"
                  }
                >
                  {v.progress}
                </span>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-6 rounded-2xl border border-neutral-700 bg-neutral-900 p-3">
          <label className="mb-1.5 block text-xs font-medium text-neutral-400">Describe the sequence</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={'e.g. "Start with video 2, cut to video 1 at the demo, alternate a couple more times, end with video 3\'s closing."'}
            rows={3}
            disabled={readyVideos.length < 2}
            className="w-full resize-none rounded-md bg-transparent text-sm text-neutral-100 placeholder:text-neutral-500 focus:outline-none disabled:opacity-40"
          />
          <button
            onClick={handleCombine}
            disabled={readyVideos.length < 2 || !prompt.trim() || busy}
            className="mt-2 rounded-md bg-gradient-to-br from-sky-500 to-indigo-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-30"
          >
            Combine {readyVideos.length >= 2 ? `${readyVideos.length} videos` : ""}
          </button>
          {readyVideos.length < 2 && (
            <p className="mt-1.5 text-xs text-neutral-500">Add at least 2 fully-extracted videos to combine.</p>
          )}
        </div>

        {error && (
          <div className="mt-4 rounded-md border border-red-900/60 bg-red-950/40 px-3 py-2 text-sm text-red-200">
            {error}
          </div>
        )}

        {summary && (
          <div className="mt-4 rounded-md border border-neutral-800 bg-neutral-900 px-3 py-2 text-sm text-neutral-300">
            {summary}
          </div>
        )}

        {resultUrl && (
          <div className="mt-4">
            <video src={resultUrl} controls autoPlay className="w-full rounded-lg border border-neutral-800" />
          </div>
        )}
      </main>
    </div>
  );
}
