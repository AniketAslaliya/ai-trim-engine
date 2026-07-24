"use client";

import { useEffect, useRef, useState } from "react";
import { Timeline } from "@/lib/api";

interface Props {
  timeline: Timeline;
  currentTime: number;
  onSeek: (t: number) => void;
  onRemoveSilence: () => void;
  onManualDelete: (start: number, end: number) => void;
  busy: boolean;
}

interface Shot {
  start: number;
  end: number;
  tags: string[];
  objects: string[];
}

// Segment color coding mirrors the fields the resolve stage actually reasons
// over (see .claude/skills/timeline-schema) — silence and filler words are the
// two boolean signals the deterministic resolver can act on directly.
function segmentClasses(seg: Timeline["segments"][number]): string {
  if (seg.is_silence) return "bg-neutral-700/60";
  if (seg.filler_words.length > 0) return "bg-amber-600/70";
  return "bg-sky-600/70";
}

// Groups per-segment shot_boundary flags into contiguous shot spans for the
// video track — the Timeline only marks *where* a new shot starts, so a shot
// is "from one shot_boundary segment up to (not including) the next one."
function computeShots(segments: Timeline["segments"]): Shot[] {
  const shots: Shot[] = [];
  for (const seg of segments) {
    if (seg.shot_boundary || shots.length === 0) {
      shots.push({ start: seg.start, end: seg.end, tags: [...seg.scene_tags], objects: [...seg.objects] });
    } else {
      const last = shots[shots.length - 1];
      last.end = seg.end;
      for (const t of seg.scene_tags) if (!last.tags.includes(t)) last.tags.push(t);
      for (const o of seg.objects) if (!last.objects.includes(o)) last.objects.push(o);
    }
  }
  return shots;
}

function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec - m * 60;
  return `${m}:${s.toFixed(m > 0 ? 0 : 1).padStart(m > 0 ? 2 : 3, "0")}`;
}

const TICK_COUNT = 8;
const CLICK_THRESHOLD_PX = 4;

export default function VideoTimeline({
  timeline,
  currentTime,
  onSeek,
  onRemoveSilence,
  onManualDelete,
  busy,
}: Props) {
  const duration = timeline.duration_sec || 1;
  const ticks = Array.from({ length: TICK_COUNT + 1 }, (_, i) => (duration / TICK_COUNT) * i);
  const shots = computeShots(timeline.segments);

  const silenceSegments = timeline.segments.filter((s) => s.is_silence);
  const silenceDuration = silenceSegments.reduce((sum, s) => sum + (s.end - s.start), 0);

  const tracksRef = useRef<HTMLDivElement>(null);
  const dragStartXRef = useRef(0);
  const [dragStartTime, setDragStartTime] = useState<number | null>(null);
  const [dragCurTime, setDragCurTime] = useState<number | null>(null);
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null);

  function timeAtClientX(clientX: number): number {
    const rect = tracksRef.current!.getBoundingClientRect();
    const frac = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
    return frac * duration;
  }

  function handleMouseDown(e: React.MouseEvent) {
    dragStartXRef.current = e.clientX;
    const t = timeAtClientX(e.clientX);
    setDragStartTime(t);
    setDragCurTime(t);
    setSelection(null);
  }

  useEffect(() => {
    if (dragStartTime === null) return;

    function handleMove(e: MouseEvent) {
      setDragCurTime(timeAtClientX(e.clientX));
    }
    function handleUp(e: MouseEvent) {
      const movedPx = Math.abs(e.clientX - dragStartXRef.current);
      if (movedPx < CLICK_THRESHOLD_PX) {
        onSeek(dragStartTime as number);
      } else {
        const endTime = timeAtClientX(e.clientX);
        setSelection({ start: Math.min(dragStartTime as number, endTime), end: Math.max(dragStartTime as number, endTime) });
      }
      setDragStartTime(null);
      setDragCurTime(null);
    }

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
    return () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragStartTime]);

  const liveSelection =
    dragStartTime !== null && dragCurTime !== null
      ? { start: Math.min(dragStartTime, dragCurTime), end: Math.max(dragStartTime, dragCurTime) }
      : selection;

  return (
    <div className="mt-3 rounded-lg border border-neutral-800 bg-neutral-950">
      <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-neutral-500">
          Timeline
        </span>
        <div className="flex items-center gap-3">
          {silenceSegments.length > 0 && (
            <button
              onClick={onRemoveSilence}
              disabled={busy}
              className="rounded-md bg-neutral-800 px-2.5 py-1 text-[11px] font-medium text-neutral-200 hover:bg-neutral-700 disabled:opacity-40"
              title={`Removes ${silenceSegments.length} silence gap(s) totaling ${silenceDuration.toFixed(1)}s`}
            >
              Remove silence &amp; gaps ({silenceSegments.length})
            </button>
          )}
          <span className="font-mono text-[11px] text-neutral-500">
            {formatTime(currentTime)} / {formatTime(duration)}
          </span>
        </div>
      </div>

      <div className="p-3">
        <div className="relative h-4 select-none">
          {ticks.map((t, i) => (
            <span
              key={i}
              className="absolute top-0 -translate-x-1/2 font-mono text-[10px] text-neutral-500"
              style={{ left: `${(t / duration) * 100}%` }}
            >
              {formatTime(t)}
            </span>
          ))}
        </div>

        <div
          ref={tracksRef}
          onMouseDown={handleMouseDown}
          className="relative cursor-crosshair select-none space-y-1"
        >
          {/* Video track — shot boundaries */}
          <div className="relative h-9 w-full overflow-hidden rounded-md border border-neutral-700 bg-neutral-900">
            <span className="absolute left-1 top-0.5 z-10 text-[9px] font-semibold uppercase tracking-wider text-neutral-600">
              Video
            </span>
            {ticks.map((t, i) => (
              <div key={i} className="absolute inset-y-0 w-px bg-neutral-800" style={{ left: `${(t / duration) * 100}%` }} />
            ))}
            {shots.map((shot, i) => (
              <div
                key={i}
                title={[shot.tags.join(", "), shot.objects.join(", ")].filter(Boolean).join(" · ") || `shot ${i + 1}`}
                className={`absolute top-0 h-full border-r border-black/50 ${i % 2 === 0 ? "bg-neutral-600/50" : "bg-neutral-500/40"}`}
                style={{
                  left: `${(shot.start / duration) * 100}%`,
                  width: `${Math.max(((shot.end - shot.start) / duration) * 100, 0.2)}%`,
                }}
              />
            ))}
          </div>

          {/* Audio track — transcript / silence / filler words */}
          <div className="relative h-9 w-full overflow-hidden rounded-md border border-neutral-700 bg-neutral-900">
            <span className="absolute left-1 top-0.5 z-10 text-[9px] font-semibold uppercase tracking-wider text-neutral-600">
              Audio
            </span>
            {ticks.map((t, i) => (
              <div key={i} className="absolute inset-y-0 w-px bg-neutral-800" style={{ left: `${(t / duration) * 100}%` }} />
            ))}
            {timeline.segments.map((seg) => (
              <div
                key={seg.id}
                title={seg.transcript || (seg.is_silence ? "silence" : `segment ${seg.id}`)}
                className={`absolute top-0 h-full border-r border-black/40 ${segmentClasses(seg)}`}
                style={{
                  left: `${(seg.start / duration) * 100}%`,
                  width: `${Math.max(((seg.end - seg.start) / duration) * 100, 0.2)}%`,
                }}
              />
            ))}
          </div>

          {/* Shared overlays: playhead + drag selection, spanning both tracks */}
          <div className="pointer-events-none absolute inset-0">
            {liveSelection && (
              <div
                className="absolute inset-y-0 border border-sky-400 bg-sky-400/20"
                style={{
                  left: `${(liveSelection.start / duration) * 100}%`,
                  width: `${((liveSelection.end - liveSelection.start) / duration) * 100}%`,
                }}
              />
            )}
            <div
              className="absolute inset-y-0 w-[2px] bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.8)]"
              style={{ left: `${(currentTime / duration) * 100}%` }}
            />
          </div>
        </div>

        {selection && (
          <div className="mt-2 flex items-center justify-between rounded-md bg-sky-950/40 px-2.5 py-1.5 text-[11px]">
            <span className="text-sky-200">
              Selected {formatTime(selection.start)} – {formatTime(selection.end)} ({(selection.end - selection.start).toFixed(1)}s)
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setSelection(null)}
                className="rounded px-2 py-0.5 text-neutral-400 hover:bg-neutral-800"
              >
                Clear
              </button>
              <button
                onClick={() => {
                  onManualDelete(selection.start, selection.end);
                  setSelection(null);
                }}
                disabled={busy}
                className="rounded bg-red-700 px-2 py-0.5 font-medium text-white hover:bg-red-600 disabled:opacity-40"
              >
                Delete selection
              </button>
            </div>
          </div>
        )}

        <p className="mt-2 text-[10px] text-neutral-600">Drag on the timeline to select a range to manually trim. Click to seek.</p>

        <div className="mt-2 flex items-center gap-4 text-[11px] text-neutral-500">
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-sky-600/70" /> speech
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-neutral-700/60" /> silence
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-sm bg-amber-600/70" /> filler words
          </span>
        </div>
      </div>
    </div>
  );
}
