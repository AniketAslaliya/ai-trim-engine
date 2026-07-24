"use client";

import { useEffect, useRef, useState } from "react";
import { Clip, Transition } from "@/lib/api";

interface Props {
  clips: Clip[];
  transitions: Transition[];
  videoNames: Record<string, string>;
  videoDurations: Record<string, number>;
  currentTime: number;
  onSeek: (t: number) => void;
  onApply: (clips: { video_id: string; start: number; end: number }[]) => void;
  busy: boolean;
}

// Fixed palette so the same source video keeps the same color across
// re-renders — order-of-first-appearance in the clip list, not video_id hash,
// so the colors stay stable while the user is looking at one sequence.
const PALETTE = [
  "bg-sky-600/70 border-sky-400/60",
  "bg-violet-600/70 border-violet-400/60",
  "bg-emerald-600/70 border-emerald-400/60",
  "bg-amber-600/70 border-amber-400/60",
  "bg-rose-600/70 border-rose-400/60",
];

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(m > 0 ? 0 : 1).padStart(m > 0 ? 2 : 3, "0")}`;
}

const TRIM_HANDLE_PX = 8;
const MIN_CLIP_SEC = 0.2;

export default function ComposeTimeline({
  clips,
  transitions,
  videoNames,
  videoDurations,
  currentTime,
  onSeek,
  onApply,
  busy,
}: Props) {
  // Local editable copy — manual reorder/trim/delete happen here instantly;
  // "Apply changes" is what actually sends it to the backend for re-render.
  const [local, setLocal] = useState(clips.map((c) => ({ video_id: c.video_id || "", start: c.start, end: c.end })));
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [dirty, setDirty] = useState(false);
  const trackRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLocal(clips.map((c) => ({ video_id: c.video_id || "", start: c.start, end: c.end })));
    setDirty(false);
    setSelected(null);
  }, [clips]);

  const colorFor = (() => {
    const order: string[] = [];
    for (const c of local) if (!order.includes(c.video_id)) order.push(c.video_id);
    const map: Record<string, string> = {};
    order.forEach((vid, i) => (map[vid] = PALETTE[i % PALETTE.length]));
    return map;
  })();

  const totalDuration = local.reduce((sum, c) => sum + (c.end - c.start), 0) || 1;
  const starts: number[] = [];
  {
    let acc = 0;
    for (const c of local) {
      starts.push(acc);
      acc += c.end - c.start;
    }
  }

  function seqTimeAtClientX(clientX: number): number {
    const rect = trackRef.current!.getBoundingClientRect();
    const frac = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
    return frac * totalDuration;
  }

  function mutate(next: typeof local) {
    setLocal(next);
    setDirty(true);
  }

  function handleDelete(i: number) {
    mutate(local.filter((_, idx) => idx !== i));
    setSelected(null);
  }

  function handleDrop(targetIndex: number) {
    if (dragIndex === null || dragIndex === targetIndex) return;
    const next = [...local];
    const [moved] = next.splice(dragIndex, 1);
    next.splice(targetIndex, 0, moved);
    mutate(next);
    setDragIndex(null);
  }

  // Trim by dragging a clip's left/right edge — clamped so the clip stays
  // within its own source video's real duration and never inverts.
  function startTrim(i: number, edge: "start" | "end") {
    return (e: React.MouseEvent) => {
      e.stopPropagation();
      e.preventDefault();
      function onMove(ev: MouseEvent) {
        const seqT = seqTimeAtClientX(ev.clientX);
        const clipSeqStart = starts[i];
        const localT = seqT - clipSeqStart; // time relative to this clip's own seq position
        setLocal((prev) => {
          const next = [...prev];
          const clip = { ...next[i] };
          const dur = videoDurations[clip.video_id] ?? Infinity;
          if (edge === "start") {
            const newStart = Math.max(0, Math.min(clip.start + localT, clip.end - MIN_CLIP_SEC));
            clip.start = newStart;
          } else {
            const newEnd = Math.min(dur, Math.max(clip.start + localT, clip.start + MIN_CLIP_SEC));
            clip.end = newEnd;
          }
          next[i] = clip;
          return next;
        });
        setDirty(true);
      }
      function onUp() {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      }
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    };
  }

  return (
    <div className="mt-3 rounded-lg border border-neutral-800 bg-neutral-950">
      <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
          Sequence timeline
        </span>
        <div className="flex items-center gap-2">
          {dirty && (
            <button
              onClick={() => onApply(local)}
              disabled={busy || local.length === 0}
              className="rounded-md bg-gradient-to-br from-sky-500 to-indigo-600 px-2.5 py-1 text-[11px] font-medium text-white hover:brightness-110 disabled:opacity-40"
            >
              Apply changes
            </button>
          )}
          <span className="font-mono text-[11px] text-neutral-500">
            {formatTime(currentTime)} / {formatTime(totalDuration)}
          </span>
        </div>
      </div>

      <div className="p-3">
        <div
          ref={trackRef}
          className="relative h-12 w-full overflow-hidden rounded-md border border-neutral-700 bg-neutral-900"
          onClick={(e) => {
            if (e.target === trackRef.current) onSeek(seqTimeAtClientX(e.clientX));
          }}
        >
          {local.map((c, i) => {
            const dur = c.end - c.start;
            const leftPct = (starts[i] / totalDuration) * 100;
            const widthPct = Math.max((dur / totalDuration) * 100, 0.3);
            const t = transitions[i];
            const hasMatch = i > 0 && local[i - 1].video_id !== c.video_id;
            return (
              <div
                key={i}
                draggable
                onDragStart={() => setDragIndex(i)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => handleDrop(i)}
                onClick={(e) => {
                  e.stopPropagation();
                  setSelected(i);
                }}
                title={`${videoNames[c.video_id] || c.video_id.slice(0, 8)} — ${dur.toFixed(1)}s${
                  hasMatch && t?.visual_score != null
                    ? `\nMatch cut: ${t.visual_score}/10, audio Δ${t.audio_delta_db}dB\n${t.visual_reason}`
                    : hasMatch
                    ? "\nCross-video join"
                    : ""
                }`}
                className={`absolute top-0 h-full cursor-grab select-none border-r-2 ${colorFor[c.video_id]} ${
                  selected === i ? "ring-2 ring-white/80" : ""
                }`}
                style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
              >
                <span className="pointer-events-none absolute left-1 top-0.5 truncate text-[9px] font-medium text-white/90">
                  {videoNames[c.video_id] || c.video_id.slice(0, 6)}
                </span>
                <div
                  onMouseDown={startTrim(i, "start")}
                  className="absolute left-0 top-0 h-full cursor-ew-resize bg-white/0 hover:bg-white/30"
                  style={{ width: TRIM_HANDLE_PX }}
                />
                <div
                  onMouseDown={startTrim(i, "end")}
                  className="absolute right-0 top-0 h-full cursor-ew-resize bg-white/0 hover:bg-white/30"
                  style={{ width: TRIM_HANDLE_PX }}
                />
                {hasMatch && (
                  <div
                    className={`absolute -left-[1px] top-0 h-full w-[2px] ${
                      t?.visual_score != null
                        ? t.visual_score >= 6
                          ? "bg-emerald-400"
                          : "bg-yellow-400"
                        : "bg-yellow-400"
                    }`}
                  />
                )}
              </div>
            );
          })}
          <div
            className="pointer-events-none absolute inset-y-0 w-[2px] bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.8)]"
            style={{ left: `${(currentTime / totalDuration) * 100}%` }}
          />
        </div>

        {selected !== null && local[selected] && (
          <div className="mt-2 flex items-center justify-between rounded-md bg-neutral-900 px-2.5 py-1.5 text-[11px]">
            <span className="text-neutral-300">
              {videoNames[local[selected].video_id] || local[selected].video_id.slice(0, 8)} — clip {selected + 1} of{" "}
              {local.length} ({(local[selected].end - local[selected].start).toFixed(1)}s)
            </span>
            <div className="flex gap-2">
              <button onClick={() => setSelected(null)} className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800">
                Deselect
              </button>
              <button
                onClick={() => handleDelete(selected)}
                disabled={busy}
                className="rounded bg-red-700 px-2 py-0.5 font-medium text-white hover:bg-red-600 disabled:opacity-40"
              >
                Delete clip
              </button>
            </div>
          </div>
        )}

        <p className="mt-2 text-[10px] text-neutral-600">
          Drag a clip to reorder. Drag its edges to trim in/out. Click a clip to select it, then delete it. Green/yellow
          marks on cross-video joins show the real match-cut score (green ≥ 6/10). Changes apply only after you click
          &quot;Apply changes&quot;.
        </p>
      </div>
    </div>
  );
}
