"use client";

import { useEffect, useRef, useState } from "react";
import { KeepRange, Timeline } from "@/lib/api";

interface Props {
  timeline: Timeline;
  keepRanges: KeepRange[] | null;
  currentTime: number;
  onSeek: (t: number) => void;
  onRemoveSilence: () => void;
  onManualDelete: (start: number, end: number) => void;
  onDenoise: () => void;
  busy: boolean;
}

type Range = [number, number];

interface Piece<T> {
  seqStart: number;
  seqEnd: number;
  item: T;
}

// Segment color coding mirrors the fields the resolve stage actually reasons
// over (see .claude/skills/timeline-schema) — silence and filler words are the
// two boolean signals the deterministic resolver can act on directly.
function segmentClasses(seg: Timeline["segments"][number]): string {
  if (seg.is_silence) return "bg-neutral-700/60";
  if (seg.filler_words.length > 0) return "bg-amber-600/70";
  return "bg-sky-600/70";
}

interface Shot {
  start: number;
  end: number;
  tags: string[];
  objects: string[];
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
const JOIN_GAP_EPSILON = 0.05; // ranges within this of each other count as "already adjacent" in the source

export default function VideoTimeline({
  timeline,
  keepRanges,
  currentTime,
  onSeek,
  onRemoveSilence,
  onManualDelete,
  onDenoise,
  busy,
}: Props) {
  const sourceDuration = timeline.duration_sec || 1;

  // The current CUT sequence, in source-time coordinates — this is what makes
  // the timeline feel like Premiere's sequence view instead of a static dump
  // of the raw extraction: removed material collapses out entirely instead
  // of sitting there as a visible gap, and what's left joins up seamlessly.
  const ranges: Range[] =
    keepRanges && keepRanges.length > 0
      ? keepRanges.map((r): Range => [r.start, r.end]).sort((a, b) => a[0] - b[0])
      : [[0, sourceDuration]];
  const sequenceDuration = ranges.reduce((sum, [s, e]) => sum + (e - s), 0) || 1;

  function seqToSource(seq: number): number {
    let acc = 0;
    for (const [s, e] of ranges) {
      const len = e - s;
      if (seq <= acc + len) return s + (seq - acc);
      acc += len;
    }
    const last = ranges[ranges.length - 1];
    return last ? last[1] : seq;
  }

  // Splits an item with a [start,end] in SOURCE time into however many
  // visible pieces it has in the current sequence (usually one, but a manual
  // cut can leave an original segment straddling two now-disjoint ranges).
  function toPieces<T extends { start: number; end: number }>(items: T[]): Piece<T>[] {
    const pieces: Piece<T>[] = [];
    for (const item of items) {
      let acc = 0;
      for (const [s, e] of ranges) {
        const lo = Math.max(item.start, s);
        const hi = Math.min(item.end, e);
        if (hi > lo) pieces.push({ seqStart: acc + (lo - s), seqEnd: acc + (hi - s), item });
        acc += e - s;
      }
    }
    return pieces;
  }

  // A join marker where the current sequence stitches together two spans
  // that were NOT adjacent in the source — i.e. an actual cut point, not
  // just where one shot happened to end and another began.
  const cutMarkers: number[] = [];
  {
    let acc = 0;
    for (let i = 0; i < ranges.length; i++) {
      acc += ranges[i][1] - ranges[i][0];
      const next = ranges[i + 1];
      if (next && next[0] - ranges[i][1] > JOIN_GAP_EPSILON) cutMarkers.push(acc);
    }
  }

  const ticks = Array.from({ length: TICK_COUNT + 1 }, (_, i) => (sequenceDuration / TICK_COUNT) * i);
  const shotPieces = toPieces(computeShots(timeline.segments));
  const segmentPieces = toPieces(timeline.segments);

  const visibleSilence = segmentPieces.filter((p) => p.item.is_silence);
  const silenceCount = new Set(visibleSilence.map((p) => p.item.id)).size;
  const silenceDuration = visibleSilence.reduce((sum, p) => sum + (p.seqEnd - p.seqStart), 0);

  const tracksRef = useRef<HTMLDivElement>(null);
  const dragStartXRef = useRef(0);
  const [dragStartTime, setDragStartTime] = useState<number | null>(null);
  const [dragCurTime, setDragCurTime] = useState<number | null>(null);
  const [selection, setSelection] = useState<{ start: number; end: number } | null>(null);

  function seqTimeAtClientX(clientX: number): number {
    const rect = tracksRef.current!.getBoundingClientRect();
    const frac = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
    return frac * sequenceDuration;
  }

  function handleMouseDown(e: React.MouseEvent) {
    dragStartXRef.current = e.clientX;
    const t = seqTimeAtClientX(e.clientX);
    setDragStartTime(t);
    setDragCurTime(t);
    setSelection(null);
  }

  // A plain click (not a drag) landing on a detected silence gap selects
  // exactly that gap instead of just seeking — one click to bring up "Delete
  // selection" on the specific pause you clicked, no need to drag by hand.
  function silenceGapAt(seqT: number) {
    return visibleSilence.find((p) => seqT >= p.seqStart && seqT <= p.seqEnd);
  }

  useEffect(() => {
    if (dragStartTime === null) return;

    function handleMove(e: MouseEvent) {
      setDragCurTime(seqTimeAtClientX(e.clientX));
    }
    function handleUp(e: MouseEvent) {
      const movedPx = Math.abs(e.clientX - dragStartXRef.current);
      if (movedPx < CLICK_THRESHOLD_PX) {
        const t = dragStartTime as number;
        onSeek(t);
        const gap = silenceGapAt(t);
        if (gap) setSelection({ start: gap.seqStart, end: gap.seqEnd });
      } else {
        const endTime = seqTimeAtClientX(e.clientX);
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
          {silenceCount > 0 && (
            <button
              onClick={onRemoveSilence}
              disabled={busy}
              className="rounded-md bg-neutral-800 px-2.5 py-1 text-[11px] font-medium text-neutral-200 hover:bg-neutral-700 disabled:opacity-40"
              title={`Removes ${silenceCount} silence gap(s) totaling ${silenceDuration.toFixed(1)}s`}
            >
              Remove silence &amp; gaps ({silenceCount})
            </button>
          )}
          <button
            onClick={onDenoise}
            disabled={busy}
            className="rounded-md bg-neutral-800 px-2.5 py-1 text-[11px] font-medium text-neutral-200 hover:bg-neutral-700 disabled:opacity-40"
            title="Runs an FFT noise-reduction pass on the audio (ffmpeg afftdn) — real signal processing, not a placeholder"
          >
            Remove background noise
          </button>
          <span className="font-mono text-[11px] text-neutral-500">
            {formatTime(currentTime)} / {formatTime(sequenceDuration)}
          </span>
        </div>
      </div>

      <div className="p-3">
        <div className="relative h-4 select-none">
          {ticks.map((t, i) => (
            <span
              key={i}
              className="absolute top-0 -translate-x-1/2 font-mono text-[10px] text-neutral-500"
              style={{ left: `${(t / sequenceDuration) * 100}%` }}
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
          {/* Video track — shot boundaries, collapsed to the current sequence */}
          <div className="relative h-9 w-full overflow-hidden rounded-md border border-neutral-700 bg-neutral-900">
            <span className="absolute left-1 top-0.5 z-10 text-[9px] font-semibold uppercase tracking-wider text-neutral-600">
              Video
            </span>
            {ticks.map((t, i) => (
              <div key={i} className="absolute inset-y-0 w-px bg-neutral-800" style={{ left: `${(t / sequenceDuration) * 100}%` }} />
            ))}
            {shotPieces.map((p, i) => (
              <div
                key={i}
                title={[p.item.tags.join(", "), p.item.objects.join(", ")].filter(Boolean).join(" · ") || "shot"}
                className={`absolute top-0 h-full border-r border-black/50 ${i % 2 === 0 ? "bg-neutral-600/50" : "bg-neutral-500/40"}`}
                style={{
                  left: `${(p.seqStart / sequenceDuration) * 100}%`,
                  width: `${Math.max(((p.seqEnd - p.seqStart) / sequenceDuration) * 100, 0.15)}%`,
                }}
              />
            ))}
          </div>

          {/* Audio track — transcript / silence / filler words, same mapping */}
          <div className="relative h-9 w-full overflow-hidden rounded-md border border-neutral-700 bg-neutral-900">
            <span className="absolute left-1 top-0.5 z-10 text-[9px] font-semibold uppercase tracking-wider text-neutral-600">
              Audio
            </span>
            {ticks.map((t, i) => (
              <div key={i} className="absolute inset-y-0 w-px bg-neutral-800" style={{ left: `${(t / sequenceDuration) * 100}%` }} />
            ))}
            {segmentPieces.map((p, i) => (
              <div
                key={i}
                title={p.item.transcript || (p.item.is_silence ? "silence" : `segment ${p.item.id}`)}
                className={`absolute top-0 h-full border-r border-black/40 ${segmentClasses(p.item)}`}
                style={{
                  left: `${(p.seqStart / sequenceDuration) * 100}%`,
                  width: `${Math.max(((p.seqEnd - p.seqStart) / sequenceDuration) * 100, 0.15)}%`,
                }}
              />
            ))}
          </div>

          {/* Shared overlays: playhead + drag selection + cut-join markers */}
          <div className="pointer-events-none absolute inset-0">
            {liveSelection && (
              <div
                className="absolute inset-y-0 border border-sky-400 bg-sky-400/20"
                style={{
                  left: `${(liveSelection.start / sequenceDuration) * 100}%`,
                  width: `${((liveSelection.end - liveSelection.start) / sequenceDuration) * 100}%`,
                }}
              />
            )}
            {cutMarkers.map((seqT, i) => (
              <div
                key={i}
                title="Cut — joined here"
                className="absolute inset-y-0 w-[2px] bg-gradient-to-b from-yellow-400 via-yellow-300 to-yellow-400"
                style={{ left: `${(seqT / sequenceDuration) * 100}%` }}
              />
            ))}
            <div
              className="absolute inset-y-0 w-[2px] bg-red-500 shadow-[0_0_4px_rgba(239,68,68,0.8)]"
              style={{ left: `${(currentTime / sequenceDuration) * 100}%` }}
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
                  onManualDelete(seqToSource(selection.start), seqToSource(selection.end));
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

        <p className="mt-2 text-[10px] text-neutral-600">
          Click a silence gap to select just that gap for removal. Drag anywhere to select a custom range. Plain click elsewhere to seek.
          {cutMarkers.length > 0 && <> Yellow marks show where cuts are joined.</>}
        </p>

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
          {cutMarkers.length > 0 && (
            <span className="flex items-center gap-1">
              <span className="inline-block h-2 w-[2px] bg-yellow-400" /> cut join
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
