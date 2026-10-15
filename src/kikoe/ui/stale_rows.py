"""Rows off screen that still show the previous note, bound again a few per frame."""

from collections.abc import Callable

from gi.repository import Gdk, Gtk

from kikoe.ui.items import TranscriptItem
from kikoe.ui.transcript_row import TranscriptRow

_BOUND_PER_FRAME = 32
"""Rows off screen bound per frame after a note is opened."""


class StaleRows:
    """Spreads binding a newly opened note's off-screen rows over later frames.

    Binding every row widget takes longer than a frame. The rows on screen are bound
    at once; the others wait here, nearest first, until the frame showing the note.
    """

    def __init__(
        self,
        list_view: Gtk.ListView,
        bound: dict[TranscriptRow, TranscriptItem],
        bind_row: Callable[[TranscriptRow, TranscriptItem], None],
    ) -> None:
        self._list_view = list_view
        self._bound = bound
        self._bind_row = bind_row
        self._positions: dict[TranscriptRow, int] = {}
        self._first_frame: int | None = None
        self._tick = 0

    def __contains__(self, row: TranscriptRow) -> bool:
        return row in self._positions

    def add(self, row: TranscriptRow, position: int) -> None:
        self._positions[row] = position

    def discard(self, row: TranscriptRow) -> None:
        """Forget ``row``, which was bound or recycled."""
        self._positions.pop(row, None)

    def bind_later(self) -> None:
        """Bind the rows added so far after the next frame has shown the note."""
        self._first_frame = None
        if self._positions and not self._tick:
            self._tick = self._list_view.add_tick_callback(self._on_tick)

    def _on_tick(self, _list: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
        frame = clock.get_frame_counter()
        if self._first_frame is None:
            self._first_frame = frame
        if frame == self._first_frame:
            return True
        # The view is at the top, so the first rows are the nearest to it.
        for row in sorted(self._positions, key=self._positions.__getitem__)[:_BOUND_PER_FRAME]:
            self._bind_row(row, self._bound[row])
        if self._positions:
            return True
        self._tick = 0
        return False
