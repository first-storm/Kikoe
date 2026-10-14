"""Serialize note edits and keep their history independent of GTK widgets."""

import asyncio
import time
from collections.abc import Callable, Coroutine
from datetime import timedelta

from kikoe.core.edit_history import EditHistory, SegmentChange, SegmentFields
from kikoe.core.models import Note
from kikoe.storage.notes import NoteRepository


class EditingSession:
    """Own optimistic segment values and pending writes for the selected note."""

    def __init__(
        self,
        notes: NoteRepository,
        start_task: Callable[[Coroutine[None, None, None]], asyncio.Task[None]],
        on_saved: Callable[[Note], None],
    ) -> None:
        self.history = EditHistory()
        self._notes = notes
        self._start_task = start_task
        self._on_saved = on_saved
        self._note: Note | None = None
        self._edited: dict[tuple[str, int], SegmentFields] = {}
        self._lock = asyncio.Lock()
        self._saves: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> bool:
        return bool(self._saves)

    def set_note(self, note: Note | None) -> None:
        """Refresh saved data without losing optimistic values for this note."""
        if self._note is not None and (note is None or note.note_id != self._note.note_id):
            self.reset()
        self._note = note

    def reset(self) -> None:
        """Discard history, retaining outstanding writes so shutdown can wait for them."""
        self.history.clear()
        self._edited.clear()

    def edit(
        self, recording_id: str, position: int, change: Callable[[SegmentFields], SegmentFields]
    ) -> None:
        before = self._fields(recording_id, position)
        if before is None:
            return
        after = change(before)
        if after == before:
            return
        self.history.record(SegmentChange(recording_id, position, before, after, time.monotonic()))
        self._write(recording_id, position, after)

    def undo(self) -> SegmentChange | None:
        change = self.history.undo()
        if change is not None:
            self._write(change.recording_id, change.position, change.before)
        return change

    def redo(self) -> SegmentChange | None:
        change = self.history.redo()
        if change is not None:
            self._write(change.recording_id, change.position, change.after)
        return change

    async def wait_saved(self) -> None:
        """Exports and shutdown must observe every edit already submitted."""
        if self._saves:
            await asyncio.wait(tuple(self._saves))

    def _fields(self, recording_id: str, position: int) -> SegmentFields | None:
        if (fields := self._edited.get((recording_id, position))) is not None:
            return fields
        recording = (
            next((r for r in self._note.recordings if r.recording_id == recording_id), None)
            if self._note
            else None
        )
        if recording is None or not 0 <= position < len(recording.segments):
            return None
        segment = recording.segments[position]
        return SegmentFields(segment.text, segment.start, segment.end)

    def _write(self, recording_id: str, position: int, fields: SegmentFields) -> None:
        if self._note is None:
            return
        self._edited[recording_id, position] = fields
        task = self._start_task(self._save(self._note.note_id, recording_id, position, fields))
        self._saves.add(task)
        task.add_done_callback(self._saves.discard)

    async def _save(
        self, note_id: str, recording_id: str, position: int, fields: SegmentFields
    ) -> None:
        async with self._lock:
            note = await self._notes.update_segment(
                note_id,
                recording_id,
                position,
                text=fields.text,
                start_ms=_milliseconds(fields.start),
                end_ms=_milliseconds(fields.end),
            )
        self._on_saved(note)


def _milliseconds(value: timedelta | None) -> int | None:
    return None if value is None else round(value / timedelta(milliseconds=1))
