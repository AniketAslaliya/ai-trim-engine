"use client";

import { useEffect, useRef, useState } from "react";

export interface ChatMessage {
  role: "user" | "assistant" | "error";
  text: string;
  onRetry?: () => void;
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

function SendIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" className="h-4 w-4">
      <path d="M2.5 2.5a.75.75 0 0 1 .943-.727l14 4a.75.75 0 0 1 0 1.454l-14 4a.75.75 0 0 1-.943-.727V9.5l7-1.5-7-1.5V2.5z" />
    </svg>
  );
}

function AssistantAvatar() {
  return (
    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-sky-500 to-indigo-600 text-[10px] font-bold text-white shadow-sm shadow-sky-900/50">
      AI
    </div>
  );
}

function ErrorAvatar() {
  return (
    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-red-900/60 text-[11px] text-red-300 shadow-sm">
      !
    </div>
  );
}

function TypingDots() {
  return (
    <span className="flex items-center gap-1 py-1">
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-neutral-400 [animation-delay:-0.2s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-neutral-400 [animation-delay:-0.1s]" />
      <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-neutral-400" />
    </span>
  );
}

export default function ChatPanel({ messages, busy, disabled, onSend }: Props) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  // Without this, a new assistant message (e.g. "processing done") lands
  // below the fold of the scrollable list and looks like no response came
  // back at all — this was reported as "not giving a response" when the
  // response was there, just invisible.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  // `disabled` (no video yet) blocks sending; `busy` (extraction/edit running)
  // does not — a prompt typed while busy is queued by the caller and applied
  // automatically once the current job finishes, so typing is never gated on
  // waiting for a spinner.
  function send() {
    if (!draft.trim() || disabled) return;
    onSend(draft.trim());
    setDraft("");
  }

  return (
    <aside className="flex h-full w-full flex-col border-l border-neutral-800 bg-gradient-to-b from-neutral-950 to-neutral-900">
      <div className="flex items-center gap-2.5 border-b border-neutral-800 px-4 py-3.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-sky-500 to-indigo-600 shadow-lg shadow-sky-950/50">
          <svg viewBox="0 0 20 20" fill="white" className="h-4 w-4">
            <path d="M10 2a1 1 0 0 1 1 1v1.06a6.01 6.01 0 0 1 4.94 4.94H17a1 1 0 1 1 0 2h-1.06a6.01 6.01 0 0 1-4.94 4.94V17a1 1 0 1 1-2 0v-1.06A6.01 6.01 0 0 1 4.06 11H3a1 1 0 1 1 0-2h1.06A6.01 6.01 0 0 1 9 4.06V3a1 1 0 0 1 1-1zm0 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8z" />
          </svg>
        </div>
        <div>
          <h2 className="text-sm font-semibold text-neutral-100">Edit assistant</h2>
          <p className="text-[11px] text-neutral-500">Each message applies on top of the current cut</p>
        </div>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto px-3.5 py-4">
        {messages.length === 0 && (
          <div className="rounded-xl border border-dashed border-neutral-800 px-3 py-4 text-center text-xs text-neutral-500">
            {disabled ? "Upload a video to get started." : "No edits yet — try a prompt below."}
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex items-end gap-2 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
            {m.role === "assistant" && <AssistantAvatar />}
            {m.role === "error" && <ErrorAvatar />}
            <div
              className={
                m.role === "user"
                  ? "max-w-[85%] rounded-2xl rounded-br-md bg-gradient-to-br from-sky-600 to-sky-700 px-3.5 py-2 text-sm text-white shadow-md shadow-sky-950/30"
                  : m.role === "error"
                  ? "max-w-[85%] rounded-2xl rounded-bl-md border border-red-900/60 bg-red-950/40 px-3.5 py-2 text-sm text-red-200"
                  : "max-w-[85%] rounded-2xl rounded-bl-md bg-neutral-800/80 px-3.5 py-2 text-sm text-neutral-100 shadow-sm"
              }
            >
              <div className="whitespace-pre-wrap leading-relaxed">{m.text}</div>
              {m.onRetry && (
                <button
                  onClick={m.onRetry}
                  className="mt-2 rounded-lg border border-red-700/70 px-2.5 py-1 text-xs font-medium text-red-100 transition hover:bg-red-800/50"
                >
                  Retry
                </button>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex items-end gap-2">
            <AssistantAvatar />
            <div className="rounded-2xl rounded-bl-md bg-neutral-800/80 px-3.5 py-1.5 shadow-sm">
              <TypingDots />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-neutral-800 bg-neutral-950/60 p-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {SAMPLE_PROMPTS.map((p) => (
            <button
              key={p}
              onClick={() => !disabled && onSend(p)}
              disabled={disabled}
              className="rounded-full border border-neutral-700 bg-neutral-900/60 px-2.5 py-1 text-[11px] text-neutral-400 transition hover:-translate-y-0.5 hover:border-sky-700 hover:text-sky-300 disabled:pointer-events-none disabled:opacity-30"
            >
              {p}
            </button>
          ))}
        </div>
        <div className="flex items-end gap-2 rounded-2xl border border-neutral-700 bg-neutral-900 p-1.5 pl-3 shadow-inner transition focus-within:border-sky-600">
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
            placeholder={
              disabled
                ? "Upload a video first..."
                : "e.g. Remove all the silences, pauses, and filler words like um and uh from this video."
            }
            rows={2}
            className="flex-1 resize-none bg-transparent py-1.5 text-sm text-neutral-100 placeholder:text-neutral-500 focus:outline-none disabled:opacity-40"
          />
          <button
            onClick={send}
            disabled={disabled || !draft.trim()}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-sky-500 to-indigo-600 text-white shadow-md shadow-sky-950/40 transition hover:brightness-110 disabled:opacity-30 disabled:shadow-none"
          >
            <SendIcon />
          </button>
        </div>
      </div>
    </aside>
  );
}
