"""File previews replace display rows, never the note they were built from."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import gi
import pytest

from kikoe.core.models import Note, Recording, RecordingSource, Segment
from kikoe.core.transcription import FileTranscriptSnapshot, LiveSnapshot, LiveState

if TYPE_CHECKING:
    from kikoe.ui.note_view import NoteView

pytestmark = pytest.mark.gtk


@pytest.fixture
def view() -> NoteView:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        pytest.skip("A display is required for GTK previews")
    Adw.init()

    from kikoe.ui.note_view import NoteView

    return NoteView()


def recording(id: str, text: str = "") -> Recording:
    return Recording(
        id,
        datetime.now(UTC),
        timedelta(seconds=5),
        RecordingSource.IMPORTED,
        "OpenAI",
        "gpt-4o-transcribe",
        (Segment(text),) if text else (),
    )


def note() -> Note:
    return Note(
        "note",
        "Meeting",
        datetime.now(UTC),
        (
            recording("before", "Before"),
            recording("target", "Original"),
            recording("after", "After"),
        ),
    )


def texts(view: NoteView) -> list[str]:
    return [
        item.segment.text for item in view._items if item.segment is not None and not item.hidden
    ]


def test_import_preview_works_without_a_saved_note(view: NoteView) -> None:
    view.begin_file_transcription(None, recording("import"))
    view.update_file_transcription(FileTranscriptSnapshot(preview=(Segment("Hello"),)))
    assert texts(view) == ["Hello"]
    header = view._items.get_item(0)
    assert header is not None and header.number == 1
    assert view.stack.get_visible_child_name() == "transcript"
    view.end_file_transcription()
    assert texts(view) == []
    assert view._note is None


def test_retranscription_stays_in_place_and_cancel_restores_original(view: NoteView) -> None:
    original = note()
    view.show_note(original)
    view.begin_file_transcription(original, recording("target"), replace_recording_id="target")
    view.update_file_transcription(
        FileTranscriptSnapshot((Segment("New sentence"),), (Segment("Partial"),))
    )
    assert texts(view) == ["Before", "New sentence", "Partial", "After"]
    assert view._file_rows[0].number == 2
    assert all(item.position is None for item in view._file_rows)
    assert original.recordings[1].segments == (Segment("Original"),)
    view.end_file_transcription()
    assert texts(view) == ["Before", "Original", "After"]


def test_note_refresh_preserves_stream_and_new_display_options(view: NoteView) -> None:
    original = note()
    view.show_note(original)
    view.begin_file_transcription(original, recording("target"), replace_recording_id="target")
    view.update_file_transcription(FileTranscriptSnapshot(preview=(Segment("Partial"),)))
    updated = replace(original, display=replace(original.display, show_speakers=False))
    view.show_note(updated)
    assert texts(view) == ["Before", "Partial", "After"]
    view.end_file_transcription()
    assert view._note == updated
    assert texts(view) == ["Before", "Original", "After"]


def test_append_stream_finalizes_preview_without_rebuilding_prefix(view: NoteView) -> None:
    original = note()
    view.show_note(original)
    view.begin_file_transcription(original, recording("import"))
    first = view._items.get_item(0)
    view.update_file_transcription(FileTranscriptSnapshot(preview=(Segment("Hello"),)))
    assert view._file_rows[-1].preview
    view.update_file_transcription(FileTranscriptSnapshot((Segment("Hello!"),)))
    assert texts(view) == ["Before", "Original", "After", "Hello!"]
    assert not view._file_rows[-1].preview
    assert view._items.get_item(0) is first
    assert view._file_rows[0].number == 4


def test_stream_rows_hide_editing_playback_and_speaker_actions(view: NoteView) -> None:
    from kikoe.ui.transcript_row import TranscriptRow

    view.begin_file_transcription(None, recording("import"))
    view.update_file_transcription(FileTranscriptSnapshot((Segment("Hello", speaker_id="A"),)))
    row = TranscriptRow()
    row.bind(
        view._file_rows[0], show_timestamps=True, show_speakers=True, compact=False, editable=True
    )
    assert not row.play_button.get_visible()
    assert not row.recording_menu_button.get_visible()
    row.bind(
        view._file_rows[1], show_timestamps=True, show_speakers=True, compact=False, editable=True
    )
    assert row.text_stack.get_visible_child_name() == "live"
    assert not row.speaker_button.get_sensitive()


def test_realtime_keeps_using_the_shared_preview_renderer(view: NoteView) -> None:
    original = note()
    view.show_note(original)
    view.show_recording("OpenAI", "gpt-4o-transcribe", realtime=True)
    view.update_live(LiveSnapshot((Segment("Final"),), (Segment("Partial"),), LiveState.LIVE))
    assert texts(view) == ["Before", "Original", "After", "Final", "Partial"]
    view.hide_recording()
    assert texts(view) == ["Before", "Original", "After"]


def test_stream_does_not_follow_bottom_when_user_is_reading_older_text(
    view: NoteView,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gi.repository import GLib

    view.begin_file_transcription(None, recording("import"))
    scheduled: list[object] = []

    def schedule(callback: object) -> int:
        scheduled.append(callback)
        return 1

    monkeypatch.setattr(GLib, "idle_add", schedule)
    adjustment = view.transcript_scroll.get_vadjustment()
    adjustment.configure(100, 0, 1000, 1, 10, 100)
    view.update_file_transcription(FileTranscriptSnapshot(preview=(Segment("Partial"),)))
    assert not scheduled
    adjustment.set_value(900)
    view.update_file_transcription(FileTranscriptSnapshot(preview=(Segment("Partial text"),)))
    assert len(scheduled) == 1


def test_display_restores_latest_preview_when_returning_to_target(view: NoteView) -> None:
    from kikoe.ui.file_transcription import FileTranscriptionDisplay

    original = note()
    display = FileTranscriptionDisplay(view)
    view.show_note(original)
    display.begin(original, recording("target"), replacing=True)
    other = replace(original, note_id="other")
    display.hide_if_other(other)
    view.show_note(other)
    display.update(FileTranscriptSnapshot((Segment("New", speaker_id="A"),)))
    assert texts(view) == ["Before", "Original", "After"]
    view.show_note(original)
    display.show(original)
    assert texts(view) == ["Before", "New", "After"]
    display.end()
    assert texts(view) == ["Before", "Original", "After"]
    assert not display.active


def test_display_ending_job_cancels_pending_preview_refresh(
    view: NoteView, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Callable

    from gi.repository import GLib

    from kikoe.ui.file_transcription import FileTranscriptionDisplay

    scheduled: list[Callable[[], bool]] = []
    removed: list[int] = []

    def schedule(_milliseconds: int, callback: Callable[[], bool]) -> int:
        scheduled.append(callback)
        return 42

    original = note()
    display = FileTranscriptionDisplay(view)
    view.show_note(original)
    display.begin(original, recording("target"), replacing=True)
    monkeypatch.setattr(GLib, "timeout_add", schedule)
    monkeypatch.setattr(GLib, "source_remove", removed.append)
    display.update(FileTranscriptSnapshot(preview=(Segment("Partial"),)))
    assert scheduled
    display.end()
    assert removed == [42]
    scheduled[0]()
    assert texts(view) == ["Before", "Original", "After"]
