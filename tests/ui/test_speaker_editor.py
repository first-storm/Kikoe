"""Automatic speaker labels are display text, not an entered person name."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from kikoe.core.models import Note, Speaker
from kikoe.storage.notes import NoteRepository

if TYPE_CHECKING:
    from kikoe.ui.note_view import NoteView

pytestmark = pytest.mark.gtk


def test_unnamed_speaker_requires_a_name_before_saving(
    view: NoteView, notes: NoteRepository
) -> None:
    from kikoe.ui.speaker_editor import SpeakerEditor

    note = Note("note", "Test", datetime.now(UTC), speakers=(Speaker("person", "", 2),))
    editor = SpeakerEditor(notes, note, "person", lambda: None)
    assert editor.name_row.get_text() == ""
    assert not editor.save_button.get_sensitive()
    editor.name_row.set_text("   ")
    assert not editor.save_button.get_sensitive()
    editor.name_row.set_text("Alice")
    assert editor.save_button.get_sensitive()


def test_named_speaker_keeps_the_saved_name(view: NoteView, notes: NoteRepository) -> None:
    from kikoe.ui.speaker_editor import SpeakerEditor

    note = Note("note", "Test", datetime.now(UTC), speakers=(Speaker("person", "Alice", 2),))
    editor = SpeakerEditor(notes, note, "person", lambda: None)
    assert editor.name_row.get_text() == "Alice"
