"""The open note: each recording's header followed by its segments, in one list.

A ``Gtk.ListView`` only creates widgets for visible rows, so transcripts with thousands
of segments stay fast.
"""

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.formatting import transcript_features
from kikoe.core.models import Note
from kikoe.core.validation import Capability
from kikoe.ui.items import TranscriptItem
from kikoe.ui.templates import ui_file
from kikoe.ui.transcript_row import TranscriptRow


@Gtk.Template(filename=ui_file("note_view"))
class NoteView(Adw.Bin):
    __gtype_name__ = "KikoeNoteView"

    stack: Gtk.Stack = Gtk.Template.Child()
    transcript_list: Gtk.ListView = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
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
    rows: list[TranscriptItem] = []
    for number, recording in enumerate(note.recordings, start=1):
        rows.append(TranscriptItem(recording, number))
        rows.extend(
            TranscriptItem(
                recording,
                number,
                segment,
                note.speaker_name(segment.speaker_id) or segment.speaker_id
                if segment.speaker_id
                else None,
            )
            for segment in recording.segments
        )
    return rows
