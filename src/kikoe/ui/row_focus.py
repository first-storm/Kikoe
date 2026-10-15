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

    def focus_segment(self, recording_id: str, position: int) -> None:
        """Scroll to a segment and put the caret at the end of its text."""
        index = self._segments.find(recording_id, position)
        if index is not None:
            self.focus_index(index, at_end=True)

    def focus_index(self, index: int, *, at_end: bool) -> None:
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
                    row.edit(at_end=at_end)
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
