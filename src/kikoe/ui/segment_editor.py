"""In-place transcript editing: forwards row edits to the editing session, with Undo and Redo."""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import replace
from datetime import timedelta

from gi.repository import Gio, GLib

from kikoe.core.edit_history import SegmentChange, SegmentFields
from kikoe.core.models import Note
from kikoe.services.editing import EditingSession
from kikoe.storage.notes import NoteRepository
from kikoe.ui.note_view import NoteView
from kikoe.ui.transcript_row import NO_TIME
from kikoe.ui.window_host import WindowHost


class SegmentEditor:
    """Connects the note view's edit signals to one `EditingSession`."""

    def __init__(
        self,
        host: WindowHost,
        notes: NoteRepository,
        note_view: NoteView,
        start_task: Callable[[Coroutine[None, None, None]], asyncio.Task[None]],
    ) -> None:
        self.blocked = False
        """Edits are refused while work is writing to the shown note."""
        self._host = host
        self._note_view = note_view
        self._session = EditingSession(notes, start_task, self._on_applied)
        note_view.connect("text-edited", self._on_note_view_text_edited)
        note_view.connect("times-edited", self._on_note_view_times_edited)
        note_view.connect("segment-left", self._on_note_view_segment_left)

    @property
    def pending(self) -> bool:
        return self._session.pending

    async def wait_saved(self) -> None:
        await self._session.wait_saved()

    async def read_note(self, note_id: str) -> Note:
        """Read a note so that `set_note` can never bring back text an edit replaced."""
        return await self._session.read_note(note_id)

    def set_note(self, note: Note | None) -> Note | None:
        """Take a newly read note; returns it with the edits that are still being saved."""
        return self._session.set_note(note)

    def forget(self) -> None:
        """Start a fresh history, e.g. after another note was opened."""
        self._session.reset()
        self.update_actions()

    def update_actions(self) -> None:
        enabled = not self.blocked and self._host.note is not None
        self._host.set_action_enabled("undo", enabled and self._session.history.can_undo)
        self._host.set_action_enabled("redo", enabled and self._session.history.can_redo)

    def on_undo(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._step(self._session.undo)

    def on_redo(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._step(self._session.redo)

    def _step(self, step: Callable[[], SegmentChange | None]) -> None:
        self._note_view.flush_edits()
        change = step()
        if change is not None:
            self._note_view.focus_segment(change.recording_id, change.position)
        self.update_actions()

    def _on_note_view_text_edited(
        self, _view: NoteView, recording_id: str, position: int, text: str
    ) -> None:
        self._edit(recording_id, position, lambda fields: replace(fields, text=text.strip()))

    def _on_note_view_times_edited(
        self, _view: NoteView, recording_id: str, position: int, start_ms: int, end_ms: int
    ) -> None:
        self._edit(
            recording_id,
            position,
            lambda fields: replace(fields, start=_from_ms(start_ms), end=_from_ms(end_ms)),
        )

    def _on_note_view_segment_left(self, _view: NoteView) -> None:
        self._session.history.seal()

    def _edit(
        self,
        recording_id: str,
        position: int,
        change: Callable[[SegmentFields], SegmentFields],
    ) -> None:
        if self.blocked:
            return
        self._session.edit(recording_id, position, change)
        self.update_actions()

    def _on_applied(self, note: Note, recording_id: str, position: int) -> None:
        self._host.apply_segment_edit(note, recording_id, position)


def _from_ms(value: int) -> timedelta | None:
    return None if value == NO_TIME else timedelta(milliseconds=value)
