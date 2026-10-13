"""Exercise real GTK allocation, lazy editors and row recycling under a display."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from kikoe.core.models import Note, Recording, RecordingSource, Segment

if TYPE_CHECKING:
    from collections.abc import Iterator

    from kikoe.ui.note_view import NoteView

pytestmark = pytest.mark.gtk


def drain() -> None:
    from gi.repository import GLib

    loop = GLib.MainLoop()
    GLib.timeout_add(120, lambda: loop.quit() or False)
    loop.run()


@pytest.fixture
def view() -> Iterator[NoteView]:
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    Adw.init()
    if Gdk.Display.get_default() is None:
        pytest.skip("GTK UI verification requires a display")
    from kikoe.ui.note_view import NoteView

    widget = NoteView()
    window = Gtk.Window(child=widget)
    window.set_default_size(960, 700)
    window.present()
    drain()
    yield widget
    window.destroy()
    drain()


def note(count: int, name: str = "n") -> Note:
    recording = Recording(
        name + "-recording",
        datetime.now(UTC),
        timedelta(),
        RecordingSource.IMPORTED,
        "Provider",
        "Model",
        tuple(Segment(f"Segment {position}") for position in range(count)),
    )
    return Note(name, name, datetime.now(UTC), (recording,))


def test_large_note_only_builds_visible_previews_and_no_segment_editors(view: NoteView) -> None:
    view.show_note(note(10_000))
    drain()
    assert view._items.get_n_items() == 10_001
    assert len(view._items._cache) < 300
    assert sum(cell.row is not None for cell in view._bound) <= 2
    assert sum(cell.get_child() is not None for cell in view._bound) < 50
    assert view.layout_ready()


def test_far_keyboard_navigation_can_edit_and_flush_before_switching(view: NoteView) -> None:
    changes: list[tuple[str, int, str]] = []

    def edited(_view: NoteView, recording: str, position: int, text: str) -> None:
        changes.append((recording, position, text))

    view.connect("text-edited", edited)
    view.show_note(note(10_000))
    drain()
    view.focus_segment("n-recording", 9000)
    drain()
    row = next((cell.row for cell, item in view._bound.items() if item.position == 9000), None)
    assert row is not None
    assert row.text_view.has_focus()
    row.text_view.get_buffer().set_text("Edited text")
    view.show_note(note(1, "next"))
    drain()
    assert changes == [("n-recording", 9000, "Edited text")]
    item = view._items.get_item(1)
    assert item is not None and item.recording.recording_id == "next-recording"


def test_timestamp_button_initializes_and_commits_the_lazy_popover(view: NoteView) -> None:
    from kikoe.ui.transcript_preview import TranscriptPreview

    original = note(1)
    recording = replace(
        original.recordings[0],
        segments=(Segment("Timed text", timedelta(seconds=2), timedelta(seconds=5)),),
    )
    view.show_note(replace(original, recordings=(recording,)))
    drain()
    cell = next(cell for cell, item in view._bound.items() if item.position == 0)
    preview = cell.get_child()
    assert isinstance(preview, TranscriptPreview)
    changes: list[tuple[str, int, int, int]] = []

    def edited(_view: NoteView, recording: str, position: int, start: int, end: int) -> None:
        changes.append((recording, position, start, end))

    view.connect("times-edited", edited)
    preview.timestamp_button.emit("clicked")
    drain()
    row = cell.row
    assert row is not None
    assert row.start_row.get_text() == "00:02"
    assert row.end_row.get_text() == "00:05"
    row.start_row.set_text("00:03")
    row.timestamp_popover.popdown()
    drain()
    assert changes == [("n-recording", 0, 3000, 5000)]


def test_in_place_text_refresh_preserves_the_editing_row_and_caret(view: NoteView) -> None:
    original = note(3)
    view.show_note(original)
    drain()
    view.focus_segment("n-recording", 1)
    drain()
    cell = next(cell for cell, item in view._bound.items() if item.position == 1)
    row = cell.row
    assert row is not None
    buffer = row.text_view.get_buffer()
    buffer.place_cursor(buffer.get_iter_at_offset(2))
    recording = replace(
        original.recordings[0], segments=(Segment("Updated"), *original.recordings[0].segments[1:])
    )
    view.show_note(replace(original, recordings=(recording,)))
    drain()
    assert cell.row is row
    assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == 2


def test_single_wrapped_segment_replaces_its_estimated_height(view: NoteView) -> None:
    original = note(1)
    recording = replace(original.recordings[0], segments=(Segment("Wrapped paragraph " * 50),))
    view.show_note(replace(original, recordings=(recording,)))
    drain()
    assert view.layout_ready()
    cell = next(cell for cell, item in view._bound.items() if item.position == 0)
    assert cell.get_height() > 32


def test_click_places_the_caret_at_a_unicode_character_not_a_byte_offset(view: NoteView) -> None:
    from gi.repository import Gtk, Pango

    from kikoe.ui.transcript_preview import TranscriptPreview

    original = note(1)
    recording = replace(original.recordings[0], segments=(Segment("你好Kikoe"),))
    view.show_note(replace(original, recordings=(recording,)))
    drain()
    cell = next(cell for cell, item in view._bound.items() if item.position == 0)
    preview = cell.get_child()
    assert isinstance(preview, TranscriptPreview)
    rectangle = preview.text_label.get_layout().index_to_pos(len("你好".encode()))
    offset_x, offset_y = preview.text_label.get_layout_offsets()
    preview._on_label_pressed(
        Gtk.GestureClick(),
        1,
        rectangle.x / Pango.SCALE + offset_x + 1,
        (rectangle.y + rectangle.height / 2) / Pango.SCALE + offset_y,
    )
    drain()
    row = cell.row
    assert row is not None
    buffer = row.text_view.get_buffer()
    assert buffer.get_iter_at_mark(buffer.get_insert()).get_offset() == 2
