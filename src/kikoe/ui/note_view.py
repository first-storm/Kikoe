"""The open note: each recording's header followed by its segments, in one list.

A ``Gtk.ListView`` only creates widgets for visible rows, so transcripts with thousands
of segments stay fast.
"""

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.formatting import SPEAKER_COLOR_COUNT, speaker_order, transcript_features
from kikoe.core.models import Note
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
        self._items.splice(0, self._items.get_n_items(), _rows(note))
        if not same_note and self._items.get_n_items():
            self.transcript_list.scroll_to(0, Gtk.ListScrollFlags.NONE, None)

    def show_recording(self, summary: str, model: str) -> None:
        """Append the live section while preserving the existing transcript above it."""
        count = len(self._note.recordings) if self._note else 0
        self.recording_view.configure(count + 1, summary, model)
        self.recording_slot.set_visible(True)
        self.stack.set_visible(count > 0)
        self.stack.set_vexpand(False)
        self.transcript_scroll.set_max_content_height(260)
        self.transcript_scroll.set_propagate_natural_height(True)

    def hide_recording(self) -> None:
        self.recording_slot.set_visible(False)
        self.stack.set_visible(True)
        self.stack.set_vexpand(True)
        self.transcript_scroll.set_propagate_natural_height(False)

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
            show_timestamps=display is not None and display.show_timestamps,
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
