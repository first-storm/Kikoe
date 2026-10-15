"""Attribute recognised speech to the speaker turns a separate diarization model found.

Local engines transcribe and diarize in two passes over the same audio. Word times,
when the model gives them, let a long segment be split where the speaker changes;
otherwise each timed segment goes to the speaker it overlaps most.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

from kikoe.core.models import Segment
from kikoe.core.transcript_words import TranscriptWord, group_words

NEAREST_LIMIT = timedelta(seconds=1)
"""Speech just outside every turn (breaths, trailing words) joins the closest one."""


@dataclass(frozen=True, slots=True)
class SpeakerTurn:
    start: timedelta
    end: timedelta
    speaker: str


@dataclass(frozen=True, slots=True)
class TimedWord:
    text: str
    start: timedelta
    end: timedelta


def speaker_at(start: timedelta, end: timedelta, turns: tuple[SpeakerTurn, ...]) -> str | None:
    """The speaker overlapping ``start`` to ``end`` the most, or the nearest one close by."""
    best: tuple[timedelta, str] | None = None
    for turn in turns:
        overlap = min(end, turn.end) - max(start, turn.start)
        if overlap > timedelta() and (best is None or overlap > best[0]):
            best = (overlap, turn.speaker)
    if best is not None:
        return best[1]
    nearest: tuple[timedelta, str] | None = None
    for turn in turns:
        gap = max(turn.start - end, start - turn.end)
        if gap <= NEAREST_LIMIT and (nearest is None or gap < nearest[0]):
            nearest = (gap, turn.speaker)
    return nearest[1] if nearest else None


def assign_speakers(
    segments: tuple[Segment, ...],
    words: tuple[TimedWord, ...],
    turns: tuple[SpeakerTurn, ...],
) -> tuple[Segment, ...]:
    """Label ``segments`` with speakers; nothing changes without turns."""
    if not turns:
        return segments
    if words:
        return group_words(
            tuple(
                TranscriptWord(w.text, w.start, w.end, speaker_at(w.start, w.end, turns))
                for w in words
            )
        )
    return tuple(
        replace(s, speaker_id=speaker_at(s.start, s.end, turns))
        if s.start is not None and s.end is not None
        else s
        for s in segments
    )
