"""A lazy Gio.ListModel, keeping row identity stable as notes and live text change."""

from gi.repository import Gio, GObject

from kikoe.core.formatting import SPEAKER_COLOR_COUNT
from kikoe.core.models import Note
from kikoe.core.transcript_index import TranscriptIndex
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.speaker_row import speaker_label


class TranscriptModel(GObject.Object, Gio.ListModel[TranscriptItem]):
    """Only wrap rows requested by GTK, instead of materializing an entire transcript."""

    def __init__(self) -> None:
        super().__init__()
        self._note: Note | None = None
        self.index = TranscriptIndex((), 0, (), {}, False, False)
        self._cache: dict[int, TranscriptItem] = {}
        self._live: list[TranscriptItem] = []

    def do_get_item_type(self) -> GObject.GType:
        return GObject.type_from_name(TranscriptItem.__gtype_name__)

    def do_get_n_items(self) -> int:
        return self.index.count + len(self._live)

    def get_n_items(self) -> int:
        return self.do_get_n_items()

    def do_get_item(self, position: int) -> TranscriptItem | None:
        if position >= self.index.count:
            offset = position - self.index.count
            return self._live[offset] if 0 <= offset < len(self._live) else None
        note = self._note
        if note is None or position < 0:
            return None
        if position not in self._cache:
            recording, _segment = self.index.locate(position)
            self._cache[position] = TranscriptItem(note.recordings[recording], recording + 1)
            self._update_item(position, self._cache[position])
        return self._cache[position]

    def get_item(self, position: int) -> TranscriptItem | None:
        return self.do_get_item(position)

    def set_note(self, note: Note, *, preserve_live: bool = False) -> None:
        """Refresh cached wrappers in place, so GTK can reuse its existing row cells."""
        old_count = self.get_n_items()
        old_base = self.index.count
        self._note = note
        self.index = TranscriptIndex.build(note)
        if not preserve_live:
            self._live.clear()
        shared = min(old_base, self.index.count)
        for position in list(self._cache):
            if position >= self.index.count:
                del self._cache[position]
            else:
                self._update_item(position, self._cache[position])
        new_count = self.get_n_items()
        if old_base != self.index.count or old_count != new_count:
            self.items_changed(shared, old_count - shared, new_count - shared)

    def _update_item(self, position: int, item: TranscriptItem) -> None:
        note = self._note
        assert note is not None
        recording_index, segment_index = self.index.locate(position)
        recording = note.recordings[recording_index]
        item.recording = recording
        item.number = recording_index + 1
        item.position = segment_index
        item.segment = None if segment_index is None else recording.segments[segment_index]
        item.preview = False
        item.live_summary = None
        item.speaker_name = None
        item.color_slot = None
        if item.segment is None or item.segment.speaker_id is None:
            return
        source = item.segment.speaker_id
        identity = self.index.identities[recording_index].get(source, source)
        person = next((s for s in note.speakers if s.speaker_id == identity), None)
        order = self.index.speaker_order[identity]
        item.speaker_name = (
            speaker_label(note, identity)
            if person
            else _("Speaker {number}").format(number=order + 1)
        )
        item.color_slot = (
            (person.ordinal - 1) if person and person.ordinal else order
        ) % SPEAKER_COLOR_COUNT

    def find(self, item: TranscriptItem) -> tuple[bool, int]:
        """Locate already materialized items without loading their neighbors."""
        for position, cached in self._cache.items():
            if cached is item:
                return True, position
        for position, cached in enumerate(self._live, self.index.count):
            if cached is item:
                return True, position
        return False, 0

    def splice(self, position: int, removed: int, additions: list[TranscriptItem]) -> None:
        """Replace a suffix of the live section without disturbing saved transcript rows."""
        offset = position - self.index.count
        if offset < 0 or offset + removed > len(self._live):
            raise IndexError(position)
        self._live[offset : offset + removed] = additions
        self.items_changed(position, removed, len(additions))
