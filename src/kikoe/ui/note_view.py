"""The open note: each recording's header followed by its segments, in one list.

A ``Gtk.ListView`` only creates widgets for visible rows, so transcripts with thousands
of segments stay fast.
"""

from datetime import UTC, datetime, timedelta

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from kikoe.core.formatting import SPEAKER_COLOR_COUNT, speaker_order, transcript_features
from kikoe.core.models import Note, Recording, RecordingSource
from kikoe.core.transcription import LiveSnapshot, LiveState
from kikoe.core.validation import Capability
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.recording_view import RecordingView
from kikoe.ui.templates import ui_file
from kikoe.ui.transcript_row import TranscriptRow


@Gtk.Template(filename=ui_file("note_view"))
class NoteView(Adw.Bin):
    __gtype_name__ = "KikoeNoteView"

    stack: Gtk.Stack = Gtk.Template.Child()
    recording_slot: Adw.Bin = Gtk.Template.Child()
    transcript_scroll: Gtk.ScrolledWindow = Gtk.Template.Child()
    transcript_list: Gtk.ListView = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.recording_view = RecordingView()
        self.recording_slot.set_child(self.recording_view)
        self._note: Note | None = None
        self._has_speakers = False
        self._has_timestamps = False
        self._live: Recording | None = None
        self._live_rows: list[TranscriptItem] = []
        self._live_summary = ""
        self._items: Gio.ListStore[TranscriptItem] = Gio.ListStore(item_type=TranscriptItem)
        self._bound: dict[TranscriptRow, TranscriptItem] = {}
        """Rows on screen, updated in place when only the display options change."""
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_factory_setup)
        factory.connect("bind", self._on_factory_bind)
        factory.connect("unbind", self._on_factory_unbind)
        self.transcript_list.set_factory(factory)
        self.transcript_list.set_model(Gtk.NoSelection(model=self._items))

    def show_note(self, note: Note) -> None:
        """Show ``note``; rows are rebuilt only when its recordings changed."""
        previous = self._note
        self._note = note
        self.stack.set_visible_child_name("transcript" if note.recordings else "no-recordings")
        same_note = previous is not None and previous.note_id == note.note_id
        if (
            previous is not None
            and same_note
            and previous.recordings == note.recordings
            and previous.speakers == note.speakers
        ):
            if previous.display != note.display:
                for row, data in self._bound.items():
                    self._bind_row(row, data)
            return
        self._has_speakers = any(
            Capability.DIARIZATION in transcript_features(r.segments) for r in note.recordings
        )
        self._has_timestamps = any(s.start is not None for r in note.recordings for s in r.segments)
        self._items.splice(0, self._items.get_n_items(), _rows(note))
        if not same_note and self._items.get_n_items():
            self.transcript_list.scroll_to(0, Gtk.ListScrollFlags.NONE, None)

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
                self._items.splice(0, self._items.get_n_items(), _rows(self._note))
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
        existing = [s for r in self._note.recordings for s in r.segments]
        all_segments = (*snapshot.segments, *snapshot.preview)
        order = speaker_order((*existing, *all_segments))
        previous_columns = self._has_speakers, self._has_timestamps
        self._has_speakers = bool(order)
        self._has_timestamps = any(s.start is not None for s in (*existing, *all_segments))
        if previous_columns != (self._has_speakers, self._has_timestamps):
            for row, item in self._bound.items():
                self._bind_row(row, item)
        number = len(self._note.recordings) + 1
        summary = _("{status} · {summary}").format(
            status=statuses[snapshot.state], summary=self._live_summary
        )
        rows = [TranscriptItem(self._live, number, live_summary=summary)]
        for index, segment in enumerate(all_segments):
            speaker = segment.speaker_id
            name = (
                self._note.speaker_name(speaker)
                or _("Speaker {number}").format(number=order[speaker] + 1)
                if speaker
                else None
            )
            color = order[speaker] % SPEAKER_COLOR_COUNT if speaker else None
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
        item.set_child(TranscriptRow())

    def _on_factory_bind(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        row, data = item.get_child(), item.get_item()
        if not isinstance(row, TranscriptRow) or not isinstance(data, TranscriptItem):
            return
        self._bound[row] = data
        self._bind_row(row, data)

    def _on_factory_unbind(self, _factory: Gtk.SignalListItemFactory, item: GObject.Object) -> None:
        assert isinstance(item, Gtk.ListItem)
        row = item.get_child()
        if isinstance(row, TranscriptRow):
            self._bound.pop(row, None)

    def _bind_row(self, row: TranscriptRow, data: TranscriptItem) -> None:
        display = self._note.display if self._note else None
        row.bind(
            data,
            show_timestamps=self._has_timestamps
            and display is not None
            and display.show_timestamps,
            show_speakers=self._has_speakers and display is not None and display.show_speakers,
        )


def _rows(note: Note) -> list[TranscriptItem]:
    order = speaker_order(s for r in note.recordings for s in r.segments)
    rows: list[TranscriptItem] = []
    for number, recording in enumerate(note.recordings, start=1):
        rows.append(TranscriptItem(recording, number))
        for segment in recording.segments:
            speaker = segment.speaker_id
            if speaker is None:
                rows.append(TranscriptItem(recording, number, segment))
                continue
            name = note.speaker_name(speaker) or _("Speaker {number}").format(
                number=order[speaker] + 1
            )
            slot = order[speaker] % SPEAKER_COLOR_COUNT
            rows.append(TranscriptItem(recording, number, segment, name, slot))
    return rows
