"""The open note: each recording's header followed by its segments, in one list.

A ``Gtk.ListView`` only creates widgets for visible rows, so transcripts with thousands
of segments stay fast. Segment text is edited in place; the view reports edits and the
window saves them and keeps the Undo history.
"""

import sys
from datetime import UTC, datetime, timedelta

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from kikoe.core.models import Note, Recording, RecordingSource
from kikoe.core.speakers import resolved_note
from kikoe.core.transcript_rows import (
    plan_overlay,
    same_structure,
    transcript_columns,
    without_recording,
)
from kikoe.core.transcription import FileTranscriptSnapshot, LiveSnapshot, LiveState
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.labels import live_status_label
from kikoe.ui.recording_view import RecordingView
from kikoe.ui.row_focus import RowFocus
from kikoe.ui.speaker_banner import update_speaker_banner
from kikoe.ui.stale_rows import StaleRows
from kikoe.ui.templates import ui_file
from kikoe.ui.transcript_row import TranscriptRow
from kikoe.ui.transcript_rows import StreamRenderer, build_rows

_ONE_LINE_ROW_HEIGHT = 32
"""The height of the shortest segment row, used to count the rows that fit in the window."""
_SPARE_ROWS = 200
"""Rows a list this short gets widgets for, as ``Gtk.ListView`` keeps about that many.

Items beyond the open note's rows are kept, hidden, up to this many, so that the next
note can reuse their widgets. Beyond it they would have no widgets to reuse.
"""
_SPARES_PER_IDLE = 8
"""Row widgets built per idle callback before the first note is opened."""


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
        self._file_recording: Recording | None = None
        self._file_base_note: Note | None = None
        self._file_context: Note | None = None
        self._file_number = 1
        self._file_base = 0
        self._file_rows: list[TranscriptItem] = []
        self._file_summary = ""
        self._file_snapshot = FileTranscriptSnapshot()
        self._file_replace_id: str | None = None
        self._compact = False
        self._editable = True
        self._items: Gio.ListStore[TranscriptItem] = Gio.ListStore(item_type=TranscriptItem)
        self._bound: dict[TranscriptRow, TranscriptItem] = {}
        """Rows on screen, updated in place when only the display options change."""
        self._row_cache: list[TranscriptRow] = []
        """Prepared or recycled widgets kept outside the list until they are needed."""
        self._stale = StaleRows(self.transcript_list, self._bound, self._bind_row)
        """Rows off screen still showing the previous note."""
        self._focus = RowFocus(
            self._items, self.transcript_list, self._bound, self._stale, self._bind_row
        )
        self._renderer = StreamRenderer(
            self._items,
            self.transcript_scroll,
            lambda: self._live is not None or self._file_recording is not None,
        )
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)
        self.transcript_list.set_factory(factory)
        self.transcript_list.set_model(Gtk.NoSelection(model=self._items))
        self._style_handler = Adw.StyleManager.get_default().connect(
            "notify::dark", self._on_style_dark_changed
        )
        self.connect("destroy", self._on_destroy)
        self._prepare_source = GLib.idle_add(self._on_prepare_idle)

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
        if self._file_recording is not None:
            recording, snapshot, target = (
                self._file_recording,
                self._file_snapshot,
                self._file_replace_id,
            )
            self.end_file_transcription()
            changed = self.show_note(note)
            self.begin_file_transcription(note, recording, replace_recording_id=target)
            self.update_file_transcription(snapshot)
            return changed
        previous = self._note
        self._note = note
        update_speaker_banner(self.speaker_banner, note)
        self.stack.set_visible_child_name("transcript" if note.recordings else "no-recordings")
        same_note = previous is not None and previous.note_id == note.note_id
        if previous is not None and same_note:
            if previous.recordings == note.recordings and previous.speakers == note.speakers:
                if previous.display != note.display:
                    self._rebind()
                return False
            if same_structure(previous, note):
                self._update_segments(note)
                return False
        self._has_timestamps, self._has_speakers = transcript_columns(
            s for r in note.recordings for s in r.segments
        )
        self._fill_rows(build_rows(note), scroll_to_top=not same_note)
        return True

    def _fill_rows(self, rows: list[TranscriptItem], *, scroll_to_top: bool = False) -> None:
        """Show ``rows`` in the items the list already has, for saved notes and file overlays.

        Replacing the items would make the list destroy and rebuild every row widget,
        about two hundred of them however short the note, so each item takes over a row
        of the note and only the row widgets are bound again. Binding them all takes
        longer than a frame, so when the view moves to the top of a newly opened note,
        the rows that fit on screen are bound at once and the others in later frames.
        """
        # Typing is saved under the row it was typed into, before the items change.
        self.flush_edits()
        on_screen = sys.maxsize
        if scroll_to_top and self._items.get_n_items():
            self.transcript_list.scroll_to(0, Gtk.ListScrollFlags.NONE, None)
            # The window's height, as the view has none before the first note is shown.
            root = self.get_root()
            height = root.get_height() if isinstance(root, Gtk.Widget) else 0
            on_screen = height // _ONE_LINE_ROW_HEIGHT + 1
        count = self._items.get_n_items()
        positions: dict[TranscriptItem, int] = {}
        for position in range(min(count, len(rows))):
            item = self._items.get_item(position)
            if item is not None:
                item.assign(rows[position])
                positions[item] = position
        if len(rows) > count:
            self._items.splice(count, 0, rows[count:])
        else:
            keep = max(len(rows), _SPARE_ROWS)
            if count > keep:
                self._items.splice(keep, count - keep, [])
            for position in range(len(rows), min(count, keep)):
                item = self._items.get_item(position)
                if item is not None:
                    item.hidden = True
        for row, item in self._bound.items():
            position = positions.get(item)
            # Spare rows have no position; they are cheap to hide, so they are bound now.
            if position is None or position < on_screen:
                self._bind_row(row, item)
            else:
                self._stale.add(row, position)
        self._stale.bind_later()

    def _update_segments(self, note: Note) -> None:
        """Swap in edited segments without rebuilding rows, keeping the caret and scroll."""
        recordings = {r.recording_id: r for r in note.recordings}
        for index in range(self._items.get_n_items()):
            item = self._items.get_item(index)
            if item is None or item.hidden:
                continue
            recording = recordings.get(item.recording.recording_id)
            if recording is None:
                continue
            item.recording = recording
            if item.segment is not None and item.position is not None:
                item.segment = recording.segments[item.position]
        self._rebind()

    def _rebind(self) -> None:
        for row, data in self._bound.items():
            self._bind_row(row, data)

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
        for row in list(self._bound):
            row.flush()

    def focus_segment(self, recording_id: str, position: int) -> None:
        """Scroll to a segment and put the caret at the end of its text."""
        self._focus.focus_segment(recording_id, position)

    def _on_row_navigate(self, row: TranscriptRow, step: int) -> None:
        self._focus.navigate(row, step)

    def _on_row_text_committed(self, row: TranscriptRow, text: str) -> None:
        item = None if row in self._stale else self._bound.get(row)
        if item is not None and item.position is not None:
            self.emit("text-edited", item.recording.recording_id, item.position, text)

    def _on_row_times_committed(self, row: TranscriptRow, start_ms: int, end_ms: int) -> None:
        item = self._bound.get(row)
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
                self._fill_rows(build_rows(self._note))
        self.recording_slot.set_visible(False)
        self.stack.set_visible(True)
        self.stack.set_vexpand(True)
        self.transcript_scroll.set_propagate_natural_height(False)

    def update_live(self, snapshot: LiveSnapshot) -> None:
        if self._live is None or self._note is None:
            return
        summary = _("{status} · {summary}").format(
            status=live_status_label(snapshot.state), summary=self._live_summary
        )
        self._live_rows = self._render_stream(
            FileTranscriptSnapshot(snapshot.segments, snapshot.preview),
            self._live,
            len(self._note.recordings) + 1,
            summary,
            self._live_rows,
            sum(1 + len(r.segments) for r in self._note.recordings),
            self._note,
        )

    def begin_file_transcription(
        self, note: Note | None, recording: Recording, *, replace_recording_id: str | None = None
    ) -> None:
        """Overlay a recording's rows without changing the saved note or its history."""
        self.end_file_transcription()
        self.hide_recording()
        self._file_base_note = note
        context = note or Note("file-preview", "", datetime.now(UTC))
        self._note = context
        self._file_recording = recording
        self._file_replace_id = replace_recording_id
        self._file_rows = []
        rows = build_rows(context)
        plan = plan_overlay(context, replace_recording_id)
        self._file_number = plan.number
        self._file_base = plan.first_row
        del rows[plan.first_row : plan.first_row + plan.replaced_rows]
        self._file_context = without_recording(context, replace_recording_id)
        self._fill_rows(rows)
        self._file_summary = _("Transcribing… · {model}").format(model=recording.model_id)
        self.stack.set_visible(True)
        self.stack.set_visible_child_name("transcript")
        self.update_file_transcription(FileTranscriptSnapshot())

    def update_file_transcription(self, snapshot: FileTranscriptSnapshot) -> None:
        if self._file_recording is None or self._file_context is None:
            return
        self._file_snapshot = snapshot
        self._file_rows = self._render_stream(
            snapshot,
            self._file_recording,
            self._file_number,
            self._file_summary,
            self._file_rows,
            self._file_base,
            self._file_context,
        )

    def end_file_transcription(self) -> None:
        if self._file_recording is None:
            return
        note = self._file_base_note
        self._file_recording = None
        self._file_rows = []
        self._file_context = None
        self._file_base_note = None
        self._note = note
        self._fill_rows(build_rows(note) if note else [])
        self._has_timestamps, self._has_speakers = transcript_columns(
            s for r in (note.recordings if note else ()) for s in r.segments
        )
        self.stack.set_visible_child_name(
            "transcript" if note and note.recordings else "no-recordings"
        )
        self._rebind()

    def _render_stream(
        self,
        snapshot: FileTranscriptSnapshot,
        recording: Recording,
        number: int,
        summary: str,
        previous_rows: list[TranscriptItem],
        base: int,
        context: Note,
    ) -> list[TranscriptItem]:
        existing = (s for r in resolved_note(context).recordings for s in r.segments)
        self._set_columns(transcript_columns((*existing, *snapshot.segments, *snapshot.preview)))
        return self._renderer.render(
            snapshot, recording, number, summary, previous_rows, base, context
        )

    def _set_columns(self, columns: tuple[bool, bool]) -> None:
        """Show the time and speaker columns when any row needs them."""
        previous = self._has_timestamps, self._has_speakers
        self._has_timestamps, self._has_speakers = columns
        if previous != columns:
            self._rebind()

    def _on_factory_setup(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        item.set_activatable(False)
        item.set_selectable(False)
        item.set_focusable(False)

    def _create_row(self) -> TranscriptRow:
        """Connect editing signals once, including for rows prepared before a note opens."""
        row = TranscriptRow()
        row.connect("text-committed", self._on_row_text_committed)
        row.connect("times-committed", self._on_row_times_committed)
        row.connect("navigate", self._on_row_navigate)
        row.connect("focus-left", self._on_row_focus_left)
        return row

    def _on_factory_bind(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        data = item.get_item()
        if not isinstance(data, TranscriptItem):
            return
        row = item.get_child()
        if not isinstance(row, TranscriptRow):
            row = self._row_cache.pop() if self._row_cache else self._create_row()
            item.set_child(row)
        self._bound[row] = data
        self._bind_row(row, data)

    def _on_factory_unbind(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        row = item.get_child()
        if isinstance(row, TranscriptRow):
            # Recycling must not drop typing that has not been saved yet.
            row.flush()
            self._bound.pop(row, None)
            self._stale.discard(row)
            item.set_child(None)
            if len(self._row_cache) < _SPARE_ROWS:
                self._row_cache.append(row)

    def set_playing(self, recording_id: str | None) -> None:
        """Keep recycled recording headers in sync with the one active player."""
        self._playing = recording_id
        for row, item in self._bound.items():
            if item.segment is None:
                row.set_playing(item.recording.recording_id == recording_id)

    def _on_style_dark_changed(self, _style: Adw.StyleManager, _spec: GObject.ParamSpec) -> None:
        for row in self._bound:
            row.update_speaker_color()

    def _on_destroy(self, _view: Gtk.Widget) -> None:
        Adw.StyleManager.get_default().disconnect(self._style_handler)
        self._row_cache.clear()
        if self._prepare_source:
            GLib.source_remove(self._prepare_source)
            self._prepare_source = 0

    def _on_prepare_idle(self) -> bool:
        """Build the row widgets before the first note opens, a few between frames."""
        count = len(self._row_cache)
        if self._note is not None or count >= _SPARE_ROWS:
            self._prepare_source = 0
            return False
        for _index in range(min(_SPARES_PER_IDLE, _SPARE_ROWS - count)):
            self._row_cache.append(self._create_row())
        return True

    def _bind_row(self, row: TranscriptRow, data: TranscriptItem) -> None:
        self._stale.discard(row)
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
        if data.segment is None:
            row.set_playing(data.recording.recording_id == self._playing)
