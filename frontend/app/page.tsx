"use client";

import { useRef, useState } from "react";
import {
  Timeline,
  getTimeline,
  outputUrl,
  pollJob,
  submitEdit,
  uploadVideo,
} from "@/lib/api";
import ChatPanel, { ChatMessage } from "@/components/ChatPanel";
import VideoTimeline from "@/components/VideoTimeline";

type Stage = "idle" | "uploading" | "extracting" | "ready" | "editing" | "error";

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

  const busy = stage === "uploading" || stage === "extracting" || stage === "editing";

  async function handleFileChosen(file: File) {
    setPreviewUrl(URL.createObjectURL(file));
    setTimeline(null);
    setMessages([]);
    setStage("uploading");
    setStatusText("Uploading...");
    try {
      const { video_id, job_id } = await uploadVideo(file);
      setVideoId(video_id);
      setStage("extracting");
      setStatusText("Extracting (transcript, scenes, silence)...");
      const job = await pollJob(job_id, (j) => setStatusText(`Extraction: ${j.status}`));
      if (job.status === "failed") {
        setMessages([{ role: "error", text: job.error || "Extraction failed." }]);
        setStage("error");
        return;
      }
      const tl = await getTimeline(video_id);
      setTimeline(tl);
      setStage("ready");
      setStatusText("");
    } catch (e) {
      setMessages([{ role: "error", text: String(e) }]);
      setStage("error");
    }
  }

  async function handleSend(prompt: string) {
    if (!videoId) return;
    setMessages((m) => [...m, { role: "user", text: prompt }]);
    setStage("editing");
    try {
      const { job_id } = await submitEdit(videoId, prompt);
      const job = await pollJob(job_id, (j) => setStatusText(`Edit: ${j.status}`));
      if (job.status === "failed") {
        setMessages((m) => [...m, { role: "error", text: job.error || "Edit failed." }]);
        setStage("ready");
        return;
      }
      setPreviewUrl(outputUrl(job.job_id));
      setMessages((m) => [
        ...m,
        { role: "assistant", text: job.edl?.summary || "Edit applied." },
      ]);
      setStage("ready");
      setStatusText("");
    } catch (e) {
      setMessages((m) => [...m, { role: "error", text: String(e) }]);
      setStage("ready");
    }
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
            disabled={busy}
            className="rounded-md bg-neutral-800 px-3 py-1.5 text-xs font-medium hover:bg-neutral-700 disabled:opacity-40"
          >
            {videoId ? "Upload different video" : "Upload video"}
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <main className="flex flex-1 flex-col overflow-y-auto p-4">
          <div className="flex flex-1 items-center justify-center rounded-lg bg-black">
            {previewUrl ? (
              <video
                ref={videoRef}
                src={previewUrl}
                controls
                onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
                className="max-h-[65vh] w-full rounded-lg"
              />
            ) : (
              <p className="text-sm text-neutral-500">Upload a video to preview it here.</p>
            )}
          </div>

          {timeline && (
            <VideoTimeline timeline={timeline} currentTime={currentTime} onSeek={handleSeek} />
          )}
        </main>

        <div className="w-[340px] shrink-0">
          <ChatPanel messages={messages} busy={busy} disabled={!videoId || stage === "extracting" || stage === "uploading"} onSend={handleSend} />
        </div>
      </div>
    </div>
  );
}
