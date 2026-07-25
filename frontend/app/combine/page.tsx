"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { EDL, composeManualEdit, composeVideos, getTimeline, outputUrl, pollJob, uploadVideo } from "@/lib/api";
import ChatPanel, { ChatMessage } from "@/components/ChatPanel";
import ComposeTimeline from "@/components/ComposeTimeline";

type VideoStatus = "uploading" | "extracting" | "ready" | "failed";

interface UploadedVideo {
  video_id: string;
  name: string;
  status: VideoStatus;
  progress: string;
}

type ComposeStage = "idle" | "composing" | "done" | "failed";

const SAMPLE_PROMPTS = [
  "Alternate between the two videos, cutting on the best moments.",
  "Start with video 1, cut to video 2's highlights, end with video 1's closing.",
  "Make it feel like one continuous scene, matching cuts where possible.",
];

export default function CombinePage() {
  const [videos, setVideos] = useState<UploadedVideo[]>([]);
  const [prompt, setPrompt] = useState("");
  const [stage, setStage] = useState<ComposeStage>("idle");
  const [progressText, setProgressText] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [edl, setEdl] = useState<EDL | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [videoDurations, setVideoDurations] = useState<Record<string, number>>({});

  const fileInputRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  const readyVideos = videos.filter((v) => v.status === "ready");
  const videoNames = Object.fromEntries(videos.map((v) => [v.video_id, v.name]));
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
        if (job.status === "done") {
          try {
            const tl = await getTimeline(video_id);
            setVideoDurations((d) => ({ ...d, [video_id]: tl.duration_sec }));
          } catch {
            // Non-critical — trim clamping just falls back to no upper bound.
          }
        }
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

  async function runCompose(currentPrompt: string) {
    setStage("composing");
    setProgressText("Working out the sequence...");
    try {
      const { job_id } = await composeVideos(readyVideos.map((v) => v.video_id), currentPrompt, videoNames);
      const job = await pollJob(job_id, (j) => setProgressText(j.progress || j.status));
      if (job.status === "failed") {
        setMessages((m) => [...m, { role: "error", text: job.error || "Compose failed.", onRetry: () => runCompose(currentPrompt) }]);
        setStage("failed");
        setProgressText("");
        return;
      }
      setEdl(job.edl);
      setResultUrl(outputUrl(job.job_id));
      setMessages((m) => [...m, { role: "assistant", text: job.edl?.summary || "Combined." }]);
      setStage("done");
      setProgressText("");
    } catch (e) {
      setMessages((m) => [...m, { role: "error", text: String(e), onRetry: () => runCompose(currentPrompt) }]);
      setStage("failed");
      setProgressText("");
    }
  }

  function handleSend(text: string) {
    if (readyVideos.length < 2) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setPrompt(text);
    runCompose(text);
  }

  async function handleApplyManualEdit(clips: { video_id: string; start: number; end: number }[]) {
    setMessages((m) => [...m, { role: "user", text: `Manually edited to ${clips.length} clip(s).` }]);
    setStage("composing");
    setProgressText("Applying manual edit...");
    try {
      const { job_id } = await composeManualEdit(readyVideos.map((v) => v.video_id), clips);
      const job = await pollJob(job_id, (j) => setProgressText(j.progress || j.status));
      if (job.status === "failed") {
        setMessages((m) => [...m, { role: "error", text: job.error || "Manual edit failed.", onRetry: () => handleApplyManualEdit(clips) }]);
        setStage("failed");
        setProgressText("");
        return;
      }
      setEdl(job.edl);
      setResultUrl(outputUrl(job.job_id));
      setMessages((m) => [...m, { role: "assistant", text: job.edl?.summary || "Manual edit applied." }]);
      setStage("done");
      setProgressText("");
    } catch (e) {
      setMessages((m) => [...m, { role: "error", text: String(e), onRetry: () => handleApplyManualEdit(clips) }]);
      setStage("failed");
      setProgressText("");
    }
  }

  function handleSeek(t: number) {
    if (videoRef.current) videoRef.current.currentTime = t;
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

      <div className="flex flex-1 overflow-hidden">
        <main className="flex flex-1 flex-col overflow-y-auto p-4">
          <p className="text-sm text-neutral-400">
            Upload 2 or more videos, then describe the sequence you want in chat — the engine picks segments from each
            and combines them, analyzing the actual boundary frames and audio at every cross-video cut to judge whether
            it reads as a natural match cut. Once composed, you can also manually reorder, trim, or delete clips
            directly on the timeline below.
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

          {readyVideos.length < 2 && (
            <p className="mt-3 text-xs text-neutral-500">Add at least 2 fully-extracted videos, then describe the sequence in chat.</p>
          )}

          {resultUrl && (
            <div className="mt-4 rounded-lg border border-neutral-800 bg-black">
              <video
                key={resultUrl}
                ref={videoRef}
                src={resultUrl}
                controls
                autoPlay
                onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                className="max-h-[420px] w-full"
              />
            </div>
          )}

          {edl && edl.clips.length > 0 && (
            <ComposeTimeline
              clips={edl.clips}
              transitions={edl.transitions}
              videoNames={videoNames}
              videoDurations={videoDurations}
              currentTime={currentTime}
              onSeek={handleSeek}
              onApply={handleApplyManualEdit}
              busy={busy}
            />
          )}
        </main>

        <div style={{ width: 360 }} className="shrink-0">
          <ChatPanel
            messages={messages}
            busy={busy}
            disabled={readyVideos.length < 2}
            onSend={handleSend}
            subtitle="Each message regenerates the sequence from scratch"
            samplePrompts={SAMPLE_PROMPTS}
            placeholder={'e.g. "Start with video 2, cut to video 1 at the demo, alternate a couple more times, end with video 3\'s closing."'}
          />
        </div>
      </div>
    </div>
  );
}
