"""Decisions about which rows of a note's transcript change, free of any widget."""

from collections.abc import Hashable, Iterable, Sequence
from dataclasses import dataclass, replace

from kikoe.core.models import Note, Segment


def transcript_columns(segments: Iterable[Segment]) -> tuple[bool, bool]:
    """Whether any segment has a time (start or end), and whether any has a speaker.

    Decides the note view's columns, the export options and a recording's summary.
    """
    has_times = has_speakers = False
    for segment in segments:
        has_times = has_times or segment.start is not None or segment.end is not None
        has_speakers = has_speakers or segment.speaker_id is not None
    return has_times, has_speakers


def same_structure(previous: Note, note: Note) -> bool:
    """Whether only segment text or timestamps differ, so rows can be updated in place."""
    if previous.speakers != note.speakers or len(previous.recordings) != len(note.recordings):
        return False
    for old, new in zip(previous.recordings, note.recordings, strict=True):
        if len(old.segments) != len(new.segments) or replace(new, segments=old.segments) != old:
            return False
        pairs = zip(old.segments, new.segments, strict=True)
        if any(a.speaker_id != b.speaker_id for a, b in pairs):
            return False
    return True


@dataclass(frozen=True, slots=True)
class OverlayPlan:
    """Where a transcription in progress appears among a note's rows."""

    number: int
    """1-based number shown in the recording header."""
    first_row: int
    """Index of the row where the overlay starts."""
    replaced_rows: int
    """How many saved rows it covers; 0 when it is appended after the last recording."""


def plan_overlay(note: Note, replacing_id: str | None) -> OverlayPlan:
    """Place a new recording's rows at the end, or over the recording ``replacing_id``."""
    recordings = note.recordings
    index = next(
        (i for i, r in enumerate(recordings) if r.recording_id == replacing_id), len(recordings)
    )
    first_row = sum(1 + len(r.segments) for r in recordings[:index])
    replaced = 1 + len(recordings[index].segments) if index < len(recordings) else 0
    return OverlayPlan(index + 1, first_row, replaced)


def without_recording(note: Note, recording_id: str | None) -> Note:
    """``note`` as if the recording had not been saved yet."""
    return replace(
        note, recordings=tuple(r for r in note.recordings if r.recording_id != recording_id)
    )


def shared_prefix(old: Sequence[Hashable], new: Sequence[Hashable]) -> int:
    """How many leading entries are equal, i.e. how many rows keep their widgets."""
    count = 0
    for before, after in zip(old, new, strict=False):
        if before != after:
            break
        count += 1
    return count
