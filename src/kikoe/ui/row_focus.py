"""Moving the caret between transcript rows, including rows that have no widget yet."""

from collections.abc import Callable

from gi.repository import Gio, GLib, Gtk

from kikoe.ui.items import TranscriptItem
from kikoe.ui.stale_rows import StaleRows
from kikoe.ui.transcript_row import TranscriptRow
from kikoe.ui.transcript_rows import SegmentIndex

_FOCUS_ATTEMPTS = 3
"""Rows are bound during the next layout, so the target may need a few idle turns."""


class RowFocus:
    """Scrolls to a row and puts the caret in it once the list has a widget for it."""

    def __init__(
        self,
        items: Gio.ListStore[TranscriptItem],
        segments: SegmentIndex,
        list_view: Gtk.ListView,
        bound: dict[TranscriptRow, TranscriptItem],
        stale: StaleRows,
        bind_row: Callable[[TranscriptRow, TranscriptItem], None],
    ) -> None:
        self._items = items
        self._segments = segments
        self._list_view = list_view
        self._bound = bound
        self._stale = stale
        self._bind_row = bind_row

    def focus_segment(
        self, recording_id: str, position: int, *, select: tuple[int, int] | None = None
    ) -> None:
        """Scroll to a segment and put the caret at the end of its text, or select a part."""
        index = self._segments.find(recording_id, position)
        if index is not None:
            self.focus_index(index, at_end=True, select=select)

    def reveal_segment(self, recording_id: str, position: int) -> None:
        """Scroll a segment into view, leaving the focus where it is."""
        index = self._segments.find(recording_id, position)
        if index is not None:
            self._list_view.scroll_to(index, Gtk.ListScrollFlags.NONE, None)

    def focused_segment(self) -> tuple[str, int] | None:
        """The recording ID and position of the saved segment holding the caret, if any."""
        for row, item in self._bound.items():
            if item.position is not None and not item.is_live and row.text_view.has_focus():
                return item.recording.recording_id, item.position
        return None

    def focus_index(
        self, index: int, *, at_end: bool, select: tuple[int, int] | None = None
    ) -> None:
        self._list_view.scroll_to(index, Gtk.ListScrollFlags.NONE, None)
        item = self._items.get_item(index)
        attempts = _FOCUS_ATTEMPTS

        def focus() -> bool:
            nonlocal attempts
            attempts -= 1
            for row, data in self._bound.items():
                if data is item:
                    if row in self._stale:
                        self._bind_row(row, data)
                    if row.edit(at_end=at_end) and select is not None:
                        buffer = row.text_view.get_buffer()
                        buffer.select_range(
                            buffer.get_iter_at_offset(select[1]),
                            buffer.get_iter_at_offset(select[0]),
                        )
                    return False
            return attempts > 0

        GLib.idle_add(focus)

    def navigate(self, row: TranscriptRow, step: int) -> None:
        """Move to the next editable row above (-1) or below (+1) ``row``."""
        current = self._bound.get(row)
        if current is None:
            return
        found = self._items.find(current)
        if not found[0]:
            return
        index = found[1] + step
        while 0 <= index < self._items.get_n_items():
            item = self._items.get_item(index)
            if item is not None and not item.hidden and not item.is_live:
                self.focus_index(index, at_end=step < 0)
                return
            index += step
