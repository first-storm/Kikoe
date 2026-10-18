"""Typing that is not committed yet is saved before edits are blocked."""

from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from kikoe.core.models import Note, Recording, RecordingSource, Segment
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository

if TYPE_CHECKING:
    from kikoe.ui.note_view import NoteView

pytestmark = pytest.mark.gtk


class Host:
    def __init__(self, note: Note) -> None:
        self.note: Note | None = note

    def set_action_enabled(self, name: str, enabled: bool) -> None:
        pass

    def apply_segment_edit(self, note: Note, recording_id: str, position: int) -> None:
        self.note = note


class Task:
    def add_done_callback(self, callback: object) -> None:
        pass


def start_task(coroutine: Coroutine[Any, Any, None]) -> Any:
    coroutine.close()  # Saving is not under test, only that the edit is accepted.
    return Task()


def test_text_typed_before_blocking_is_kept(view: NoteView, tmp_path: Any) -> None:
    from kikoe.ui.segment_editor import SegmentEditor

    recording = Recording(
        "a",
        datetime.now(UTC),
        timedelta(seconds=1),
        RecordingSource.IMPORTED,
        "OpenAI",
        "gpt-4o-transcribe",
        (Segment("Old"),),
    )
    note = Note("note", "Meeting", datetime.now(UTC), (recording,))
    repository = NoteRepository(Library(tmp_path / "library", tmp_path / "audio"))
    editor = SegmentEditor(Host(note), repository, view, start_task)  # pyright: ignore[reportArgumentType]  # a stub host
    editor.set_note(note)
    # A row whose typing is still waiting for its auto-save delay commits it when flushed.
    view.flush_edits = lambda: view.emit("text-edited", "a", 0, "Typed")  # pyright: ignore[reportAttributeAccessIssue]  # stands in for a row with pending typing

    editor.set_blocked(True)

    assert editor.blocked
    assert editor._session.history.can_undo  # pyright: ignore[reportPrivateUsage]  # the edit was recorded
