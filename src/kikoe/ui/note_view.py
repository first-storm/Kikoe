"""The open note: each recording's header followed by its segments, in one list.

The list model wraps rows lazily, and offscreen cells use cheap size estimates.
Reading widgets are created near the viewport; full editors are used on interaction.
The window saves edits and keeps the Undo history.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from gi.repository import Adw, GLib, GObject, Gtk

from kikoe.core.formatting import SPEAKER_COLOR_COUNT, speaker_order
from kikoe.core.models import Note, Recording, RecordingSource
from kikoe.core.speakers import (
    live_speaker_numbers,
    resolved_note,
    unresolved_recordings,
)
from kikoe.core.transcription import LiveSnapshot, LiveState
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.recording_view import RecordingView
from kikoe.ui.templates import ui_file
from kikoe.ui.transcript_cell import TranscriptCell
from kikoe.ui.transcript_model import TranscriptModel
from kikoe.ui.transcript_preview import TranscriptPreview
from kikoe.ui.transcript_row import TranscriptRow


@Gtk.Template(filename=ui_file("note_view"))
class NoteView(Adw.BreakpointBin):
    __gtype_name__ = "KikoeNoteView"

    speaker_banner: Adw.Banner = Gtk.Template.Child()
    stack: Gtk.Stack = Gtk.Template.Child()
    recording_slot: Adw.Bin = Gtk.Template.Child()
    transcript_scroll: Gtk.ScrolledWindow = Gtk.Template.Child()
    transcript_list: Gtk.ListView = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.recording_view = RecordingView()
        self.recording_slot.set_child(self.recording_view)
        self._note: Note | None = None
        self._playing: str | None = None
        self._has_speakers = False
        self._has_timestamps = False
        self._live: Recording | None = None
        self._live_rows: list[TranscriptItem] = []
        self._live_summary = ""
        self._compact = False
        self._editable = True
        self._items = TranscriptModel()
        self._bound: dict[TranscriptCell, TranscriptItem] = {}
        """Rows on screen, updated in place when only the display options change."""
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)
        factory.connect("teardown", self._on_factory_teardown)
        self.transcript_list.set_factory(factory)
        self.transcript_list.set_model(Gtk.NoSelection(model=self._items))
        # Editors are reusable UI infrastructure, independent of any loaded note.
        self._row_pool = [self._new_row() for _ in range(2)]
        self._preview_pool: list[TranscriptPreview] = []
        self._cell_pool: list[TranscriptCell] = []

    __gsignals__ = {  # noqa: RUF012  # PyGObject reads this class attribute once
        # (recording id, position, text): a segment's text was typed over.
        "text-edited": (GObject.SignalFlags.RUN_FIRST, None, (str, int, str)),
        # (recording id, position, start ms, end ms); transcript_row.NO_TIME clears one.
        "times-edited": (GObject.SignalFlags.RUN_FIRST, None, (str, int, int, int)),
        # The caret left a segment, which ends the current Undo step.
        "segment-left": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def show_note(self, note: Note) -> bool:
        """Show ``note``; rows are rebuilt only when its recordings changed.

        Returns whether the transcript's structure changed (recordings or segments added,
        removed or replaced), which makes earlier Undo steps meaningless.
        """
        previous = self._note
        self._note = note
        pending = unresolved_recordings(note)
        self.speaker_banner.set_action_name(None)
        self.speaker_banner.set_revealed(bool(pending))
        if pending:
            recording = pending[0]
            self.speaker_banner.set_title(
                _("Recording {number} has speakers that are not yet matched.").format(
                    number=note.recordings.index(recording) + 1
                )
            )
            self.speaker_banner.set_action_target_value(GLib.Variant("s", recording.recording_id))
            self.speaker_banner.set_action_name("win.match-speakers")
        self.stack.set_visible_child_name("transcript" if note.recordings else "no-recordings")
        same_note = previous is not None and previous.note_id == note.note_id
        if (
            previous is not None
            and same_note
            and previous.recordings == note.recordings
            and previous.speakers == note.speakers
        ):
            if previous.display != note.display:
                self._rebind()
            return False
        if previous is not None and same_note and _same_structure(previous, note):
            self._update_segments(note)
            return False
        self.flush_edits()
        if not same_note:
            for cell in tuple(self._bound):
                cell.leave_editor()
        self._items.set_note(note)
        self._live_rows = []
        self._has_speakers = self._items.index.has_speakers
        self._has_timestamps = self._items.index.has_timestamps
        self._rebind()
        if not same_note and self._items.get_n_items():
            self.transcript_list.scroll_to(0, Gtk.ListScrollFlags.NONE, None)
            for cell, data in self._bound.items():
                index = (
                    self._items.index.offsets[data.number - 1]
                    + (data.position or 0)
                    + (data.position is not None)
                )
                if index < 16:
                    cell.prepare()
        return True

    def _update_segments(self, note: Note) -> None:
        """Swap in edited segments without rebuilding rows, keeping the caret and scroll."""
        self._items.set_note(note, preserve_live=self._live is not None)
        self._has_speakers = self._items.index.has_speakers
        self._has_timestamps = self._items.index.has_timestamps
        self._rebind()

    def _rebind(self) -> None:
        for cell, data in self._bound.items():
            cell.bind(data, self._bind_row)
            self._set_cell_columns(cell)

    def set_editable(self, editable: bool) -> None:
        """Lock the text while a capture or import may change the note underneath."""
        if editable == self._editable:
            return
        if not editable:
            self.flush_edits()
        self._editable = editable
        self._rebind()

    def flush_edits(self) -> None:
        """Commit typing that has not been saved yet, e.g. before Undo or leaving the note."""
        for cell in list(self._bound):
            if cell.row is not None:
                cell.row.flush()

    def layout_ready(self) -> bool:
        """Whether allocated transcript rows have their full measured height."""
        for cell in self._bound:
            if not cell.get_mapped():
                continue
            child = cell.get_child()
            if child is None:
                return False
            minimum, _natural, _baseline, _natural_baseline = child.measure(
                Gtk.Orientation.VERTICAL, cell.get_width()
            )
            if cell.get_height() < minimum:
                return False
        return True

    def focus_segment(self, recording_id: str, position: int) -> None:
        """Scroll to a segment and put the caret at the end of its text."""
        if self._note is None:
            return
        for recording_index, recording in enumerate(self._note.recordings):
            if recording.recording_id == recording_id and 0 <= position < len(recording.segments):
                self._focus_index(
                    self._items.index.offsets[recording_index] + 1 + position, at_end=True
                )
                return

    def _focus_index(self, index: int, *, at_end: bool) -> None:
        self.transcript_list.scroll_to(index, Gtk.ListScrollFlags.NONE, None)
        item = self._items.get_item(index)
        attempts = 3

        def focus() -> bool:
            # Rows are bound during the next layout, so the target may not exist yet.
            nonlocal attempts
            attempts -= 1
            for cell, data in self._bound.items():
                if data is item:
                    cell.ensure_row().edit(at_end=at_end)
                    return False
            return attempts > 0

        GLib.idle_add(focus)

    def _on_row_navigate(self, row: TranscriptRow, step: int) -> None:
        current = self._row_item(row)
        if current is None:
            return
        found = self._items.find(current)
        if not found[0]:
            return
        index = found[1] + step
        while 0 <= index < self._items.get_n_items():
            item = self._items.get_item(index)
            if item is not None and _editable_item(item):
                self._focus_index(index, at_end=step < 0)
                return
            index += step

    def _on_row_text_committed(self, row: TranscriptRow, text: str) -> None:
        item = self._row_item(row)
        if item is not None and item.position is not None:
            self.emit("text-edited", item.recording.recording_id, item.position, text)

    def _on_row_times_committed(self, row: TranscriptRow, start_ms: int, end_ms: int) -> None:
        item = self._row_item(row)
        if item is not None and item.position is not None:
            self.emit("times-edited", item.recording.recording_id, item.position, start_ms, end_ms)

    def _on_row_focus_left(self, _row: TranscriptRow) -> None:
        self.emit("segment-left")

    @Gtk.Template.Callback()
    def _on_compact_breakpoint_apply(self, _breakpoint: Adw.Breakpoint) -> None:
        self._compact = True
        self._rebind()

    @Gtk.Template.Callback()
    def _on_compact_breakpoint_unapply(self, _breakpoint: Adw.Breakpoint) -> None:
        self._compact = False
        self._rebind()

    def show_recording(self, summary: str, model: str, *, realtime: bool = False) -> None:
        """Append the live section while preserving the existing transcript above it."""
        count = len(self._note.recordings) if self._note else 0
        self._live_summary = summary
        if realtime:
            if self._live is None:
                self._live = Recording(
                    "live", datetime.now(UTC), timedelta(), RecordingSource.RECORDED, "", model
                )
                self._live_rows = []
            self.stack.set_visible(True)
            self.stack.set_visible_child_name("transcript")
            self.recording_slot.set_visible(False)
            self.update_live(LiveSnapshot((), (), LiveState.CONNECTING))
            return
        self.recording_view.configure(count + 1, summary, model)
        self.recording_slot.set_visible(True)
        self.stack.set_visible(count > 0)
        self.stack.set_vexpand(False)
        self.transcript_scroll.set_max_content_height(260)
        self.transcript_scroll.set_propagate_natural_height(True)

    def hide_recording(self) -> None:
        if self._live is not None:
            self._live = None
            self._live_rows = []
            if self._note is not None:
                self._items.set_note(self._note)
                self._rebind()
        self.recording_slot.set_visible(False)
        self.stack.set_visible(True)
        self.stack.set_vexpand(True)
        self.transcript_scroll.set_propagate_natural_height(False)

    def update_live(self, snapshot: LiveSnapshot) -> None:
        if self._live is None or self._note is None:
            return
        statuses = {
            LiveState.CONNECTING: _("Connecting…"),
            LiveState.LIVE: _("In progress"),
            LiveState.RECONNECTING: _("Reconnecting… Audio is saved locally"),
            LiveState.CATCHING_UP: _("Catching up…"),
            LiveState.INCOMPLETE: _("Transcription incomplete"),
            LiveState.PAUSED: _("Paused"),
        }
        existing = [s for r in resolved_note(self._note).recordings for s in r.segments]
        all_segments = (*snapshot.segments, *snapshot.preview)
        order = speaker_order((*existing, *all_segments))
        numbers = live_speaker_numbers(self._note, all_segments)
        previous_columns = self._has_speakers, self._has_timestamps
        self._has_speakers = bool(order)
        self._has_timestamps = any(
            s.start is not None or s.end is not None for s in (*existing, *all_segments)
        )
        if previous_columns != (self._has_speakers, self._has_timestamps):
            self._rebind()
        number = len(self._note.recordings) + 1
        summary = _("{status} · {summary}").format(
            status=statuses[snapshot.state], summary=self._live_summary
        )
        rows = [TranscriptItem(self._live, number, live_summary=summary)]
        for index, segment in enumerate(all_segments):
            speaker = segment.speaker_id
            name = (
                self._note.speaker_name(speaker)
                or _("Speaker {number}").format(number=numbers[speaker])
                if speaker
                else None
            )
            color = (numbers[speaker] - 1) % SPEAKER_COLOR_COUNT if speaker else None
            preview = index >= len(snapshot.segments)
            old = self._live_rows[index + 1] if index + 1 < len(self._live_rows) else None
            if old is not None and (old.segment, old.preview, old.speaker_name, old.color_slot) == (
                segment,
                preview,
                name,
                color,
            ):
                rows.append(old)
            else:
                rows.append(
                    TranscriptItem(self._live, number, segment, name, color, preview=preview)
                )
        adjustment = self.transcript_scroll.get_vadjustment()
        follow = adjustment.get_upper() - adjustment.get_value() - adjustment.get_page_size() < 48
        prefix = 0
        for old, new in zip(self._live_rows, rows, strict=False):
            if (old.segment, old.preview, old.live_summary) != (
                new.segment,
                new.preview,
                new.live_summary,
            ):
                break
            prefix += 1
        base = sum(1 + len(recording.segments) for recording in self._note.recordings)
        self._items.splice(base + prefix, len(self._live_rows) - prefix, rows[prefix:])
        self._live_rows = rows
        if follow:
            GLib.idle_add(self._follow_live)

    def _follow_live(self) -> bool:
        adjustment = self.transcript_scroll.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return False

    def _on_factory_setup(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        item.set_activatable(False)
        item.set_selectable(False)
        item.set_focusable(False)
        item.set_child(self._cell_pool.pop() if self._cell_pool else self._new_cell())

    def _new_cell(self) -> TranscriptCell:
        return TranscriptCell(
            self._create_row, self._release_row, self._create_preview, self._release_preview
        )

    def _create_preview(self, ensure_row: Callable[[], TranscriptRow]) -> TranscriptPreview:
        preview = self._preview_pool.pop() if self._preview_pool else TranscriptPreview()
        preview.set_editor_factory(ensure_row)
        return preview

    def _release_preview(self, preview: TranscriptPreview) -> None:
        preview.set_editor_factory(None)
        self._preview_pool.append(preview)

    def _on_factory_teardown(
        self, _factory: Gtk.SignalListItemFactory, item: GObject.Object
    ) -> None:
        assert isinstance(item, Gtk.ListItem)
        cell = item.get_child()
        if isinstance(cell, TranscriptCell):
            item.set_child(None)
            self._cell_pool.append(cell)

    def _create_row(self) -> TranscriptRow:
        return self._row_pool.pop() if self._row_pool else self._new_row()

    def _release_row(self, row: TranscriptRow) -> None:
        self._row_pool.append(row)

    def _new_row(self) -> TranscriptRow:
        row = TranscriptRow()
        row.connect("text-committed", self._on_row_text_committed)
        row.connect("times-committed", self._on_row_times_committed)
        row.connect("navigate", self._on_row_navigate)
        row.connect("focus-left", self._on_row_focus_left)
        return row

    def _row_item(self, row: TranscriptRow) -> TranscriptItem | None:
        return next((data for cell, data in self._bound.items() if cell.row is row), None)

    def _on_factory_bind(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        cell, data = item.get_child(), item.get_item()
        if not isinstance(cell, TranscriptCell) or not isinstance(data, TranscriptItem):
            return
        self._bound[cell] = data
        cell.bind(data, self._bind_row)
        self._set_cell_columns(cell)

    def _set_cell_columns(self, cell: TranscriptCell) -> None:
        display = self._note.display if self._note else None
        cell.set_columns(
            speakers=self._has_speakers and display is not None and display.show_speakers,
            timestamps=self._has_timestamps and display is not None and display.show_timestamps,
            compact=self._compact,
        )

    def _on_factory_unbind(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        cell = item.get_child()
        if isinstance(cell, TranscriptCell):
            # Recycling must not drop typing that has not been saved yet.
            cell.unbind()
            self._bound.pop(cell, None)

    def set_playing(self, recording_id: str | None) -> None:
        """Keep recycled recording headers in sync with the one active player."""
        self._playing = recording_id
        for cell, item in self._bound.items():
            if cell.row is not None:
                cell.row.set_playing(
                    item.segment is None and item.recording.recording_id == recording_id
                )

    def _bind_row(self, row: TranscriptRow | TranscriptPreview, data: TranscriptItem) -> None:
        display = self._note.display if self._note else None
        row.bind(
            data,
            show_timestamps=self._has_timestamps
            and display is not None
            and display.show_timestamps,
            show_speakers=self._has_speakers and display is not None and display.show_speakers,
            compact=self._compact,
            editable=self._editable,
        )
        if isinstance(row, TranscriptRow):
            row.set_playing(data.segment is None and data.recording.recording_id == self._playing)


def _editable_item(item: TranscriptItem) -> bool:
    return item.position is not None and not item.preview and item.recording.recording_id != "live"


def _same_structure(previous: Note, note: Note) -> bool:
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
