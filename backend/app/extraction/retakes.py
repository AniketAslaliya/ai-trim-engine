"""Retake detection: flags an earlier segment as a superseded "duplicate
take" when a later segment's transcript is highly similar to it.

This is a real, testable heuristic (transcript similarity via difflib), not
a trained retake-classifier — it will miss retakes with materially different
wording and can false-positive on genuinely repeated phrases. It is
deliberately conservative (bias toward missing a retake over wrongly
flagging real distinct content) and documented as a heuristic in the PRD,
not oversold as ground truth.
"""
from difflib import SequenceMatcher

from app.schemas import Segment

SIMILARITY_THRESHOLD = 0.6


def mark_retakes(segments: list[Segment]) -> None:
    """Mutates segments in place, setting is_duplicate_take=True on any
    non-silence segment whose transcript closely matches a LATER segment's
    transcript — the earlier one is very likely a redone line, not the take
    that was kept. Needs the full segment list (temporal order matters)."""
    speech = [s for s in segments if not s.is_silence and s.transcript.strip()]
    for i, a in enumerate(speech):
        a_text = a.transcript.lower()
        for b in speech[i + 1:]:
            ratio = SequenceMatcher(None, a_text, b.transcript.lower()).ratio()
            if ratio > SIMILARITY_THRESHOLD:
                a.is_duplicate_take = True
                break
