"""Serialize note edits and keep their history independent of GTK widgets."""

import asyncio
import time
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import replace
from datetime import timedelta

from kikoe.core.edit_history import (
    EditHistory,
    EditStep,
    SegmentChange,
    SegmentFields,
    apply_segment_fields,
    segment_fields,
)
from kikoe.core.models import Note
from kikoe.storage.library import SegmentUpdate
from kikoe.storage.notes import NoteRepository

type SegmentApplied = Callable[[Note, str, int], None]
"""Called with the edited note, the recording ID and the segment's position."""

type SegmentValue = tuple[str, int, SegmentFields]
"""A segment's recording ID, position and the values it is given."""


class EditingSession:
    """Own the selected note's optimistic segment values and its pending writes.

    An edit is shown at once and saved in the background, one write at a time. Values
    that are not saved yet are laid over a note that is read again meanwhile, so a
    reload (e.g. after a speaker rename) cannot bring back older text.
    """

    def __init__(
        self,
        notes: NoteRepository,
        start_task: Callable[[Coroutine[None, None, None]], asyncio.Task[None]],
        on_applied: SegmentApplied,
    ) -> None:
        self.history = EditHistory()
        self._notes = notes
        self._start_task = start_task
        self._on_applied = on_applied
        self._note: Note | None = None
        self._unsaved: dict[tuple[str, int], SegmentFields] = {}
        self._lock = asyncio.Lock()
        self._saves: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> bool:
        return bool(self._saves)

    def set_note(self, note: Note | None) -> Note | None:
        """Take a newly read ``note`` and return it with the values not saved yet."""
        if self._note is not None and (note is None or note.note_id != self._note.note_id):
            self.reset()
        for (recording_id, position), fields in self._unsaved.items():
            if note is not None and segment_fields(note, recording_id, position) is not None:
                note = apply_segment_fields(note, recording_id, position, fields)
        if note is not None and not self.history.matches(
            lambda recording_id, position: segment_fields(note, recording_id, position)
        ):
            self.history.clear()
        self._note = note
        return note

    def reset(self) -> None:
        """Discard history, retaining outstanding writes so shutdown can wait for them."""
        self.history.clear()
        self._unsaved.clear()

    def edit(
        self, recording_id: str, position: int, change: Callable[[SegmentFields], SegmentFields]
    ) -> None:
        if self._note is None:
            return
        before = segment_fields(self._note, recording_id, position)
        if before is None:
            return
        after = change(before)
        if after == before:
            return
        self.history.record(SegmentChange(recording_id, position, before, after, time.monotonic()))
        self._write(((recording_id, position, after),))

    def edit_texts(self, texts: Mapping[tuple[str, int], str]) -> None:
        """Give segments new text as one Undo step, e.g. Replace All."""
        if self._note is None:
            return
        changes: list[SegmentChange] = []
        now = time.monotonic()
        for (recording_id, position), text in texts.items():
            before = segment_fields(self._note, recording_id, position)
            if before is not None and before.text != text:
                after = replace(before, text=text)
                changes.append(SegmentChange(recording_id, position, before, after, now))
        if not changes:
            return
        self.history.record_all(changes)
        self._write(tuple((c.recording_id, c.position, c.after) for c in changes))

    def undo(self) -> EditStep | None:
        step = self.history.undo()
        if step is not None:
            self._write(tuple((c.recording_id, c.position, c.before) for c in step))
        return step

    def redo(self) -> EditStep | None:
        step = self.history.redo()
        if step is not None:
            self._write(tuple((c.recording_id, c.position, c.after) for c in step))
        return step

    async def wait_saved(self) -> None:
        """Exports and shutdown must observe every edit already submitted."""
        if self._saves:
            await asyncio.wait(tuple(self._saves))

    async def read_note(self, note_id: str) -> Note:
        """Read a note in order with the edits being saved.

        Edits submitted earlier are in the result; later ones are still unsaved when it
        is passed to `set_note`, which lays them over it. Reading outside this order
        could return text from before a save that had already stopped being laid over.
        """
        async with self._lock:
            return await self._notes.get_note(note_id)

    def _write(self, values: Sequence[SegmentValue]) -> None:
        """Show ``values`` at once and save them in the background, in one transaction."""
        if self._note is None:
            return
        values = [v for v in values if segment_fields(self._note, v[0], v[1]) is not None]
        if not values:
            return
        for recording_id, position, fields in values:
            self._note = apply_segment_fields(self._note, recording_id, position, fields)
            self._unsaved[recording_id, position] = fields
        for recording_id, position, _fields in values:
            self._on_applied(self._note, recording_id, position)
        task = self._start_task(self._save(self._note.note_id, values))
        self._saves.add(task)
        task.add_done_callback(self._saves.discard)

    async def _save(self, note_id: str, values: Sequence[SegmentValue]) -> None:
        updates = [
            SegmentUpdate(
                recording_id,
                position,
                fields.text,
                _milliseconds(fields.start),
                _milliseconds(fields.end),
            )
            for recording_id, position, fields in values
        ]
        async with self._lock:
            await self._notes.update_segments(note_id, updates)
        # A later edit of the same segment is still unsaved and must stay laid over reloads.
        for recording_id, position, fields in values:
            key = recording_id, position
            if self._unsaved.get(key) is fields:
                del self._unsaved[key]


def _milliseconds(value: timedelta | None) -> int | None:
    return None if value is None else round(value / timedelta(milliseconds=1))
