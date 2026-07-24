"""Single source of truth for Timeline / Intent / EDL.

Field names here must match `.claude/skills/timeline-schema/SKILL.md` exactly —
that file is the canonical reference; this is its implementation.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Word(BaseModel):
    text: str
    start: float
    end: float


class Segment(BaseModel):
    id: int
    start: float
    end: float
    transcript: str = ""
    words: list[Word] = Field(default_factory=list)
    speaker: Optional[str] = None
    is_silence: bool = False
    shot_boundary: bool = False
    scene_tags: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    audio_events: list[str] = Field(default_factory=list)
    filler_words: list[Word] = Field(default_factory=list)


class Timeline(BaseModel):
    video_id: str
    duration_sec: float
    segments: list[Segment]


class Constraints(BaseModel):
    max_duration_sec: Optional[float] = None
    min_segment_gap_sec: float = 0.1
    aspect_ratio: Optional[str] = None


class Intent(BaseModel):
    operation: Literal["filter", "rank_select", "reorder", "constrain_only"]
    mode: Literal["keep", "remove"] = "keep"
    predicate: str
    target_signal: list[str] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)


class Clip(BaseModel):
    segment_ids: list[int]
    start: float
    end: float


class Transition(BaseModel):
    at_clip_boundary: int
    type: Literal["audio_fade", "cut"] = "audio_fade"
    duration_sec: float = 0.03


class EDL(BaseModel):
    video_id: str
    clips: list[Clip]
    transitions: list[Transition] = Field(default_factory=list)
    summary: str = ""


class TimeRange(BaseModel):
    start: float
    end: float


class ManualEditRequest(BaseModel):
    """A user-drawn selection to cut, straight from the timeline UI — no LLM
    involved, so this path is instant and free (see resolve/manual.py)."""
    remove_ranges: list[TimeRange]


class JobStatus(BaseModel):
    job_id: str
    video_id: str
    kind: Literal["extraction", "edit"]
    status: Literal["pending", "running", "done", "failed"]
    error: Optional[str] = None
    intent: Optional[Intent] = None
    edl: Optional[EDL] = None
    output_path: Optional[str] = None
