"use client";

import { useState } from "react";

export interface ChatMessage {
  role: "user" | "assistant" | "error";
  text: string;
}

const SAMPLE_PROMPTS = [
  "Remove pauses and silences.",
  "Remove filler words (um, uh, hmm).",
  "Keep only outdoor scenes.",
  "Remove all laughing.",
  "Keep only questions.",
  "Make this under 30 seconds.",
];

interface Props {
  messages: ChatMessage[];
  busy: boolean;
  disabled: boolean;
  onSend: (prompt: string) => void;
}

export default function ChatPanel({ messages, busy, disabled, onSend }: Props) {
  const [draft, setDraft] = useState("");

  function send() {
    if (!draft.trim() || busy || disabled) return;
    onSend(draft.trim());
    setDraft("");
  }

  return (
    <aside className="flex h-full w-full flex-col border-l border-neutral-800 bg-neutral-950">
      <div className="border-b border-neutral-800 px-4 py-3">
        <h2 className="text-sm font-semibold text-neutral-200">Edit assistant</h2>
        <p className="text-xs text-neutral-500">Describe an edit — each message applies on top of the current cut.</p>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {messages.length === 0 && (
          <p className="text-xs text-neutral-500">
            {disabled ? "Upload a video to get started." : "No edits yet — try a prompt below."}
          </p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={
              m.role === "user"
                ? "ml-6 rounded-md bg-sky-900/40 px-3 py-2 text-sm text-sky-100"
                : m.role === "error"
                ? "mr-6 rounded-md bg-red-900/40 px-3 py-2 text-sm text-red-200"
                : "mr-6 rounded-md bg-neutral-800 px-3 py-2 text-sm text-neutral-200"
            }
          >
            {m.text}
          </div>
        ))}
        {busy && <div className="mr-6 text-xs text-neutral-500">Working...</div>}
      </div>

      <div className="border-t border-neutral-800 p-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {SAMPLE_PROMPTS.map((p) => (
            <button
              key={p}
              onClick={() => !disabled && !busy && onSend(p)}
              disabled={disabled || busy}
              className="rounded-full border border-neutral-700 px-2.5 py-1 text-[11px] text-neutral-400 hover:bg-neutral-800 disabled:opacity-30"
            >
              {p}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            disabled={disabled}
            placeholder={disabled ? "Waiting for video..." : "e.g. remove pauses and silences"}
            rows={2}
            className="flex-1 resize-none rounded-md border border-neutral-700 bg-neutral-900 p-2 text-sm text-neutral-100 placeholder:text-neutral-500 disabled:opacity-40"
          />
          <button
            onClick={send}
            disabled={disabled || busy || !draft.trim()}
            className="self-end rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-30"
          >
            Send
          </button>
        </div>
      </div>
    </aside>
  );
}
