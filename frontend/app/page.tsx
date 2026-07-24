"use client";

import { useRef, useState } from "react";
import {
  Timeline,
  getTimeline,
  manualEdit,
  outputUrl,
  pollJob,
  retryExtraction,
  submitEdit,
  uploadVideo,
} from "@/lib/api";
import ChatPanel, { ChatMessage } from "@/components/ChatPanel";
import VideoTimeline from "@/components/VideoTimeline";

type Stage = "idle" | "uploading" | "extracting" | "ready" | "editing" | "extraction_failed";

export default function Home() {
  const [stage, setStage] = useState<Stage>("idle");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<Timeline | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [statusText, setStatusText] = useState("");

  const fileInputRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  // Prompts sent while extraction is still running get queued here and drain
  // automatically once the Timeline is ready — the user shouldn't have to
  // wait for a spinner before they're allowed to type (see chat: "first
  // upload video, write prompt ... then pipeline starts actioning").
  const promptQueue = useRef<string[]>([]);

  const busy = stage === "uploading" || stage === "extracting" || stage === "editing";

  async function runExtraction(video_id: string, job_id: string) {
    setStage("extracting");
    setStatusText("Extracting (transcript, scenes, silence)...");
    try {
      const job = await pollJob(job_id, (j) => setStatusText(`Extraction: ${j.status}`));
      if (job.status === "failed") {
        setMessages((m) => [
          ...m,
          {
            role: "error",
            text: `Extraction failed: ${job.error || "unknown error"}`,
            onRetry: () => retryExtractionFlow(video_id),
          },
        ]);
        setStage("extraction_failed");
        setStatusText("Extraction failed.");
        return;
      }
      const tl = await getTimeline(video_id);
      setTimeline(tl);
      setStage("ready");
      setStatusText("");
      // Drain any prompts queued while we were extracting, in order.
      const queued = promptQueue.current;
      promptQueue.current = [];
      for (const p of queued) await runEdit(video_id, p);
    } catch (e) {
      // A poll can throw outright (not just resolve with status "failed") if
      // the job_id stops existing mid-poll — e.g. a dev server `--reload`
      // restart wipes the in-memory job store. The uploaded file on disk
      // survives that, so Retry re-running extraction against it is the
      // correct recovery, not a dead end.
      setMessages((m) => [
        ...m,
        { role: "error", text: String(e), onRetry: () => retryExtractionFlow(video_id) },
      ]);
      setStage("extraction_failed");
      setStatusText("Extraction failed.");
    }
  }

  async function retryExtractionFlow(video_id: string) {
    setStatusText("Retrying extraction...");
    try {
      const { job_id } = await retryExtraction(video_id);
      await runExtraction(video_id, job_id);
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "error", text: String(e), onRetry: () => retryExtractionFlow(video_id) },
      ]);
      setStage("extraction_failed");
      setStatusText("Extraction failed.");
    }
  }

  async function handleFileChosen(file: File) {
    setPreviewUrl(URL.createObjectURL(file));
    setTimeline(null);
    setMessages([]);
    promptQueue.current = [];
    setStage("uploading");
    setStatusText("Uploading...");
    try {
      const { video_id, job_id } = await uploadVideo(file);
      setVideoId(video_id);
      await runExtraction(video_id, job_id);
    } catch (e) {
      setMessages([{ role: "error", text: String(e) }]);
      setStage("extraction_failed");
      setStatusText("Upload failed.");
    }
  }

  async function runEdit(video_id: string, prompt: string) {
    setStage("editing");
    try {
      const { job_id } = await submitEdit(video_id, prompt);
      const job = await pollJob(job_id, (j) => setStatusText(`Edit: ${j.status}`));
      if (job.status === "failed") {
        setMessages((m) => [
          ...m,
          {
            role: "error",
            text: `Edit failed: ${job.error || "unknown error"}`,
            onRetry: () => runEdit(video_id, prompt),
          },
        ]);
        setStage("ready");
        setStatusText("Edit failed.");
        return;
      }
      setPreviewUrl(outputUrl(job.job_id));
      setMessages((m) => [...m, { role: "assistant", text: job.edl?.summary || "Edit applied." }]);
      setStage("ready");
      setStatusText("");
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "error", text: String(e), onRetry: () => runEdit(video_id, prompt) },
      ]);
      setStage("ready");
      setStatusText("Edit failed.");
    }
  }

  async function handleManualDelete(video_id: string, start: number, end: number) {
    setMessages((m) => [
      ...m,
      { role: "user", text: `Manually delete ${start.toFixed(1)}s – ${end.toFixed(1)}s` },
    ]);
    setStage("editing");
    setStatusText("Applying manual trim...");
    try {
      const { job_id } = await manualEdit(video_id, [{ start, end }]);
      const job = await pollJob(job_id, (j) => setStatusText(`Manual trim: ${j.status}`));
      if (job.status === "failed") {
        setMessages((m) => [
          ...m,
          {
            role: "error",
            text: `Manual trim failed: ${job.error || "unknown error"}`,
            onRetry: () => handleManualDelete(video_id, start, end),
          },
        ]);
        setStage("ready");
        setStatusText("Manual trim failed.");
        return;
      }
      setPreviewUrl(outputUrl(job.job_id));
      setMessages((m) => [...m, { role: "assistant", text: job.edl?.summary || "Manual trim applied." }]);
      setStage("ready");
      setStatusText("");
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "error", text: String(e), onRetry: () => handleManualDelete(video_id, start, end) },
      ]);
      setStage("ready");
      setStatusText("Manual trim failed.");
    }
  }

  function handleSend(prompt: string) {
    if (!videoId) return;
    setMessages((m) => [...m, { role: "user", text: prompt }]);
    if (stage === "uploading" || stage === "extracting") {
      promptQueue.current.push(prompt);
      setMessages((m) => [
        ...m,
        { role: "assistant", text: "Got it — I'll apply this as soon as processing finishes." },
      ]);
      return;
    }
    runEdit(videoId, prompt);
  }

  function handleSeek(t: number) {
    if (videoRef.current) videoRef.current.currentTime = t;
  }

  return (
    <div className="flex h-screen flex-col bg-neutral-950 text-neutral-100">
      <header className="flex items-center justify-between border-b border-neutral-800 px-4 py-2.5">
        <div>
          <h1 className="text-sm font-semibold">AI Trim Engine</h1>
          {statusText && <p className="text-xs text-neutral-500">{statusText}</p>}
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleFileChosen(f);
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={stage === "uploading"}
            className="rounded-md bg-neutral-800 px-3 py-1.5 text-xs font-medium hover:bg-neutral-700 disabled:opacity-40"
          >
            {videoId ? "Upload different video" : "Upload video"}
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <main className="flex flex-1 flex-col overflow-y-auto p-4">
          <div className="flex flex-1 flex-col overflow-hidden rounded-lg border border-neutral-800 bg-neutral-950">
            <div className="border-b border-neutral-800 px-3 py-1.5">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
                Program
              </span>
            </div>
            <div className="flex flex-1 items-center justify-center bg-black">
              {previewUrl ? (
                <video
                  ref={videoRef}
                  src={previewUrl}
                  controls
                  onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                  className="max-h-[60vh] w-full"
                />
              ) : (
                <p className="text-sm text-neutral-500">Upload a video to preview it here.</p>
              )}
            </div>
          </div>

          {videoId && !timeline && (
            <div className="mt-3 flex h-[124px] flex-col items-center justify-center rounded-lg border border-neutral-800 bg-neutral-950 text-xs text-neutral-500">
              {stage === "extraction_failed" ? "Extraction failed — see chat for details." : "Processing timeline..."}
            </div>
          )}
          {timeline && videoId && (
            <VideoTimeline
              timeline={timeline}
              currentTime={currentTime}
              onSeek={handleSeek}
              onRemoveSilence={() => handleSend("Remove pauses and silences.")}
              onManualDelete={(start, end) => handleManualDelete(videoId, start, end)}
              busy={busy}
            />
          )}
        </main>

        <div className="w-[340px] shrink-0">
          <ChatPanel messages={messages} busy={busy} disabled={!videoId} onSend={handleSend} />
        </div>
      </div>
    </div>
  );
}
