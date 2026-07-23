---
name: timeline-schema
description: Canonical JSON schemas for Timeline, Intent, and EDL — the three artifacts that connect every pipeline stage. Load before writing or modifying any extraction, intent-parsing, resolution, or render code, so field names/types stay consistent across the codebase instead of being re-invented per file.
---

# Timeline / Intent / EDL schemas

These three schemas are the contract between pipeline stages. Treat them as fixed once code depends on them — extend with new optional fields rather than renaming/removing existing ones.

## 1. Timeline (output of extraction stage, input to intent resolution)

One Timeline per source video, cached — never regenerated per prompt.

```json
{
  "video_id": "string",
  "duration_sec": 612.4,
  "segments": [
    {
      "id": 0,
      "start": 0.0,
      "end": 4.2,
      "transcript": "so today we're gonna, um, talk about",
      "words": [{"text": "so", "start": 0.0, "end": 0.15}, "..."],
      "speaker": "SPEAKER_00",
      "is_silence": false,
      "shot_boundary": true,
      "scene_tags": ["office", "indoor"],
      "objects": ["laptop", "desk"],
      "audio_events": [],
      "filler_words": [{"text": "um", "start": 1.8, "end": 2.0}]
    }
  ]
}
```

Segment boundaries come from shot detection first, then split further at silence gaps — segments should be small enough (a few seconds) that filtering at segment granularity doesn't lose precision, but not so small that every word is its own segment.

- `scene_tags` / `objects`: from one VLM caption call per shot keyframe, not per frame.
- `audio_events`: laughter, clapping, music — from audio event detection, populate only if the signal exists; empty list is valid, never fabricate a tag.
- `speaker`: null if diarization wasn't run or single-speaker video.

## 2. Intent (output of LLM intent-parsing stage)

One per user prompt. This is the ONLY place free-form natural language gets converted to structure — everything downstream is deterministic or operates on already-structured predicates.

```json
{
  "operation": "filter",
  "mode": "remove",
  "predicate": "segments where the speaker is laughing or audio_events contains laughter",
  "target_signal": ["audio_events", "transcript"],
  "constraints": {
    "max_duration_sec": null,
    "min_segment_gap_sec": 0.1,
    "aspect_ratio": null
  }
}
```

- `operation`: one of `filter` | `rank_select` | `reorder` | `constrain_only`. Vague prompts ("make it shorter") resolve to `constrain_only` with an implicit filter (e.g. rank by importance, drop lowest-ranked until duration target met) — don't invent a fifth operation type for vague prompts, compose the existing three.
- `predicate`: natural language, resolved against the Timeline in the next stage (by an LLM call, or deterministic matching for simple cases like `is_silence == true`). Keep it as text, not a fixed enum — this is what lets novel prompts generalize without new code.
- `constraints.max_duration_sec` etc.: only set when the user gave an explicit or inferable constraint ("under 30 seconds", "for Instagram Reels" → aspect_ratio "9:16").

## 3. EDL — Edit Decision List (output of resolution stage, input to render stage)

The only thing ffmpeg ever consumes. Never skip straight from Intent to render.

```json
{
  "video_id": "string",
  "clips": [
    {"segment_ids": [3, 4, 5], "start": 12.1, "end": 18.7}
  ],
  "transitions": [
    {"at_clip_boundary": 0, "type": "audio_fade", "duration_sec": 0.03}
  ],
  "summary": "Removed 4 silence gaps and 2 filler-word clusters, kept speaker segments only."
}
```

`clips` are in final output order — `reorder` operations change ordering here, not in the Timeline. `segment_ids` is kept for traceability back to the Timeline (debugging: "why was this cut here").
