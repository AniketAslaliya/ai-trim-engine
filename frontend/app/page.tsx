"use client";

import { useRef, useState } from "react";
import { EDL, Intent, JobStatus, outputUrl, pollJob, submitEdit, uploadVideo } from "@/lib/api";

// Subset of .claude/skills/eval-harness/SKILL.md's 20 sample prompts, spanning
// the deterministic and semantic categories, as one-click starting points.
const SAMPLE_PROMPTS = [
  "Remove pauses and silences.",
  "Remove filler words (um, uh, hmm).",
  "Keep only outdoor scenes.",
  "Remove all laughing.",
  "Keep only questions.",
  "Make this under 30 seconds.",
];

type Stage = "idle" | "uploading" | "extracting" | "ready" | "editing" | "done" | "error";

export default function Home() {
  const [stage, setStage] = useState<Stage>("idle");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [sourcePreviewUrl, setSourcePreviewUrl] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");
  const [statusText, setStatusText] = useState("");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [intent, setIntent] = useState<Intent | null>(null);
  const [edl, setEdl] = useState<EDL | null>(null);
  const [resultUrl, setResultUrl] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleFileChosen(file: File) {
    setErrorText(null);
    setSourcePreviewUrl(URL.createObjectURL(file));
    setStage("uploading");
    setStatusText("Uploading...");
    try {
      const { video_id, job_id } = await uploadVideo(file);
      setVideoId(video_id);
      setStage("extracting");
      setStatusText("Extracting (transcribing, detecting scenes/silence)...");
      const job = await pollJob(job_id, (j) => setStatusText(`Extraction: ${j.status}`));
      if (job.status === "failed") {
        setErrorText(job.error || "Extraction failed.");
        setStage("error");
        return;
      }
      setStage("ready");
      setStatusText("Ready — describe the edit you want.");
    } catch (e) {
      setErrorText(String(e));
      setStage("error");
    }
  }

  async function handleSubmitPrompt() {
    if (!videoId || !prompt.trim()) return;
    setErrorText(null);
    setIntent(null);
    setEdl(null);
    setResultUrl(null);
    setStage("editing");
    setStatusText("Parsing intent...");
    try {
      const { job_id } = await submitEdit(videoId, prompt.trim());
      const job = await pollJob(job_id, (j: JobStatus) => {
        if (j.intent) setIntent(j.intent);
        setStatusText(`Edit: ${j.status}`);
      });
      if (job.status === "failed") {
        setErrorText(job.error || "Edit failed.");
        setStage("error");
        return;
      }
      setIntent(job.intent);
      setEdl(job.edl);
      setResultUrl(outputUrl(job.job_id));
      setStage("done");
      setStatusText("Done.");
    } catch (e) {
      setErrorText(String(e));
      setStage("error");
    }
  }

  const busy = stage === "uploading" || stage === "extracting" || stage === "editing";

  return (
    <main className="mx-auto max-w-3xl px-6 py-10 font-sans">
      <h1 className="text-2xl font-semibold">AI Trim Engine</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Upload a video, describe the edit in plain language, get a trimmed cut.
      </p>

      <section className="mt-8">
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
          className="rounded-md bg-black px-4 py-2 text-white disabled:opacity-40"
        >
          {videoId ? "Upload a different video" : "Upload video"}
        </button>

        {sourcePreviewUrl && (
          <video src={sourcePreviewUrl} controls className="mt-4 w-full rounded-md border" />
        )}
      </section>

      {statusText && (
        <p className="mt-4 text-sm text-neutral-600">
          {busy && <span className="mr-2 inline-block animate-pulse">●</span>}
          {statusText}
        </p>
      )}

      {errorText && (
        <pre className="mt-4 whitespace-pre-wrap rounded-md bg-red-50 p-3 text-sm text-red-700">
          {errorText}
        </pre>
      )}

      {(stage === "ready" || stage === "editing" || stage === "done" || (stage === "error" && videoId)) && (
        <section className="mt-8">
          <label className="block text-sm font-medium">What edit do you want?</label>
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="e.g. remove pauses and silences"
            className="mt-2 w-full rounded-md border p-3 text-sm"
            rows={2}
          />
          <div className="mt-2 flex flex-wrap gap-2">
            {SAMPLE_PROMPTS.map((p) => (
              <button
                key={p}
                onClick={() => setPrompt(p)}
                className="rounded-full border px-3 py-1 text-xs text-neutral-600 hover:bg-neutral-100"
              >
                {p}
              </button>
            ))}
          </div>
          <button
            onClick={handleSubmitPrompt}
            disabled={busy || !prompt.trim()}
            className="mt-3 rounded-md bg-black px-4 py-2 text-white disabled:opacity-40"
          >
            Run edit
          </button>
        </section>
      )}

      {intent && (
        <section className="mt-8">
          <h2 className="text-sm font-semibold text-neutral-700">Parsed intent</h2>
          <pre className="mt-2 overflow-x-auto rounded-md bg-neutral-50 p-3 text-xs">
            {JSON.stringify(intent, null, 2)}
          </pre>
        </section>
      )}

      {edl && (
        <section className="mt-4">
          <h2 className="text-sm font-semibold text-neutral-700">Edit summary</h2>
          <p className="mt-2 text-sm text-neutral-600">{edl.summary}</p>
        </section>
      )}

      {resultUrl && (
        <section className="mt-6">
          <h2 className="text-sm font-semibold text-neutral-700">Result</h2>
          <video src={resultUrl} controls className="mt-2 w-full rounded-md border" />
        </section>
      )}
    </main>
  );
}
