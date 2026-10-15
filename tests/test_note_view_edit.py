"""An edited segment is shown in its own row without rebuilding the others."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import gi
import pytest

from kikoe.core.edit_history import SegmentFields, apply_segment_fields
from kikoe.core.models import Note, Recording, RecordingSource, Segment

if TYPE_CHECKING:
    from kikoe.ui.note_view import NoteView

pytestmark = pytest.mark.gtk


@pytest.fixture
def view() -> NoteView:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        pytest.skip("A display is required for GTK views")
    Adw.init()

    from kikoe.ui.note_view import NoteView

    return NoteView()


def recording(recording_id: str, count: int) -> Recording:
    return Recording(
        recording_id,
        datetime.now(UTC),
        timedelta(seconds=count),
        RecordingSource.IMPORTED,
        "OpenAI",
        "gpt-4o-transcribe",
        tuple(Segment(f"{recording_id} {i}") for i in range(count)),
    )


def note(note_id: str = "note") -> Note:
    return Note(note_id, "Meeting", datetime.now(UTC), (recording("a", 500), recording("b", 500)))


def texts(view: NoteView) -> list[str]:
    return [
        item.segment.text for item in view._items if item.segment is not None and not item.hidden
    ]


def test_editing_a_segment_changes_only_its_row(view: NoteView) -> None:
    shown = note()
    view.show_note(shown)
    before = texts(view)
    edited = apply_segment_fields(shown, "b", 7, SegmentFields("Edited"))

    view.update_segment(edited, "b", 7)

    after = texts(view)
    assert after[500 + 7] == "Edited"
    assert after[: 500 + 7] == before[: 500 + 7]
    assert after[500 + 8 :] == before[500 + 8 :]


def test_edits_keep_working_after_the_note_is_shown_again(view: NoteView) -> None:
    shown = note()
    view.show_note(shown)
    shorter = replace(shown, recordings=(recording("b", 3),))
    view.show_note(shorter)

    view.update_segment(apply_segment_fields(shorter, "b", 2, SegmentFields("Edited")), "b", 2)

    assert texts(view) == ["b 0", "b 1", "Edited"]


def test_an_edit_of_another_note_shows_that_note(view: NoteView) -> None:
    view.show_note(note())
    other = note("other")

    view.update_segment(apply_segment_fields(other, "a", 0, SegmentFields("Edited")), "a", 0)

    assert texts(view)[0] == "Edited"
    assert len(texts(view)) == 1000
