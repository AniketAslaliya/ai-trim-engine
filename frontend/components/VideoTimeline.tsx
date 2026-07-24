"use client";

import { Timeline } from "@/lib/api";

interface Props {
  timeline: Timeline;
  currentTime: number;
  onSeek: (t: number) => void;
}

// Segment color coding mirrors the fields the resolve stage actually reasons
// over (see .claude/skills/timeline-schema) — silence and filler words are the
// two boolean signals the deterministic resolver can act on directly.
function segmentClasses(seg: Timeline["segments"][number]): string {
  if (seg.is_silence) return "bg-neutral-700/60";
  if (seg.filler_words.length > 0) return "bg-amber-600/70";
  return "bg-sky-600/70";
}

export default function VideoTimeline({ timeline, currentTime, onSeek }: Props) {
  const duration = timeline.duration_sec || 1;

  function handleClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const frac = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
    onSeek(frac * duration);
  }

  return (
    <div className="mt-3">
      <div
        onClick={handleClick}
        className="relative h-14 w-full cursor-pointer overflow-hidden rounded-md border border-neutral-700 bg-neutral-900"
      >
        {timeline.segments.map((seg) => (
          <div
            key={seg.id}
            title={
              seg.transcript ||
              (seg.is_silence ? "silence" : seg.scene_tags.join(", ") || `segment ${seg.id}`)
            }
            className={`absolute top-0 h-full border-r border-black/40 ${segmentClasses(seg)}`}
            style={{
              left: `${(seg.start / duration) * 100}%`,
              width: `${Math.max(((seg.end - seg.start) / duration) * 100, 0.2)}%`,
            }}
          >
            {seg.shot_boundary && (
              <div className="absolute inset-y-0 left-0 w-[2px] bg-white/70" />
            )}
          </div>
        ))}
        <div
          className="absolute inset-y-0 w-[2px] bg-red-500"
          style={{ left: `${(currentTime / duration) * 100}%` }}
        />
      </div>
      <div className="mt-1 flex items-center gap-4 text-[11px] text-neutral-400">
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-sm bg-sky-600/70" /> speech
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-sm bg-neutral-700/60" /> silence
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-2 rounded-sm bg-amber-600/70" /> filler words
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-[2px] bg-white/70" /> shot boundary
        </span>
      </div>
    </div>
  );
}
