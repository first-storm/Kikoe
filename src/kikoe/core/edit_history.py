"""Note-wide Undo/Redo history for in-place transcript edits.

Edits are autosaved while typing, so several saves of one segment in quick succession
are merged into one step; otherwise Undo would only remove a few characters at a time.
"""

from dataclasses import dataclass, replace
from datetime import timedelta

from kikoe.core.models import Note

MERGE_WINDOW = 2.0
"""Seconds between saves of one segment that still count as the same typing step."""


@dataclass(frozen=True, slots=True)
class SegmentFields:
    """The editable part of a segment."""

    text: str
    start: timedelta | None = None
    end: timedelta | None = None


def segment_fields(note: Note, recording_id: str, position: int) -> SegmentFields | None:
    """The editable part of a segment, or None when the note has no such segment."""
    recording = note.recording(recording_id)
    if recording is None or not 0 <= position < len(recording.segments):
        return None
    segment = recording.segments[position]
    return SegmentFields(segment.text, segment.start, segment.end)


def apply_segment_fields(
    note: Note, recording_id: str, position: int, fields: SegmentFields
) -> Note:
    """``note`` with one segment's editable part replaced; everything else is shared."""
    recordings = list(note.recordings)
    index = next(i for i, r in enumerate(recordings) if r.recording_id == recording_id)
    recording = recordings[index]
    segments = list(recording.segments)
    segments[position] = replace(
        segments[position], text=fields.text, start=fields.start, end=fields.end
    )
    recordings[index] = replace(recording, segments=tuple(segments))
    return replace(note, recordings=tuple(recordings))


@dataclass(frozen=True, slots=True)
class SegmentChange:
    """One step of the history: a segment going from ``before`` to ``after``.

    ``at`` is a monotonic time in seconds, used only to merge typing.
    """

    recording_id: str
    position: int
    before: SegmentFields
    after: SegmentFields
    at: float


class EditHistory:
    """Undo and redo stacks of segment changes for one open note."""

    __slots__ = ("_redo", "_sealed", "_undo")

    def __init__(self) -> None:
        self._undo: list[SegmentChange] = []
        self._redo: list[SegmentChange] = []
        self._sealed = True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def record(self, change: SegmentChange) -> None:
        """Add ``change``, merging it into the previous step while the user keeps typing."""
        if change.before == change.after:
            return
        self._redo.clear()
        previous = self._undo[-1] if self._undo else None
        if previous is not None and not self._sealed and _continues(previous, change):
            merged = replace(change, before=previous.before)
            if merged.before == merged.after:
                self._undo.pop()
            else:
                self._undo[-1] = merged
        else:
            self._undo.append(change)
        self._sealed = False

    def seal(self) -> None:
        """End the current typing step, e.g. when the caret leaves a segment."""
        self._sealed = True

    def undo(self) -> SegmentChange | None:
        """The step to revert (apply its ``before``), or None when there is none."""
        if not self._undo:
            return None
        change = self._undo.pop()
        self._redo.append(change)
        self._sealed = True
        return change

    def redo(self) -> SegmentChange | None:
        """The step to reapply (apply its ``after``), or None when there is none."""
        if not self._redo:
            return None
        change = self._redo.pop()
        self._undo.append(change)
        self._sealed = True
        return change

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._sealed = True


def _continues(previous: SegmentChange, change: SegmentChange) -> bool:
    """Whether ``change`` is more typing in the segment ``previous`` was editing."""
    return (
        previous.recording_id == change.recording_id
        and previous.position == change.position
        and previous.after == change.before
        and _text_only(previous)
        and _text_only(change)
        and change.at - previous.at < MERGE_WINDOW
    )


def _text_only(change: SegmentChange) -> bool:
    return (change.before.start, change.before.end) == (change.after.start, change.after.end)
