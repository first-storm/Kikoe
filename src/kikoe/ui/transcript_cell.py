"""Delay editor construction until GTK actually allocates a row in the viewport."""

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from kikoe.ui.items import TranscriptItem
from kikoe.ui.templates import ui_file
from kikoe.ui.transcript_preview import TranscriptPreview
from kikoe.ui.transcript_row import TranscriptRow


@Gtk.Template(filename=ui_file("transcript_cell"))
class TranscriptCell(Adw.Bin):
    """A cheap measurement placeholder for ListView's offscreen preallocated items."""

    __gtype_name__ = "KikoeTranscriptCell"

    def __init__(
        self,
        create_row: Callable[[], TranscriptRow],
        release_row: Callable[[TranscriptRow], None],
        create_preview: Callable[[Callable[[], TranscriptRow]], TranscriptPreview],
        release_preview: Callable[[TranscriptPreview], None],
    ) -> None:
        super().__init__()
        self.set_layout_manager(None)
        self.row: TranscriptRow | None = None
        self._create_row = create_row
        self._release_row = release_row
        self._create_preview = create_preview
        self._release_preview = release_preview
        self._bind_row: (
            Callable[[TranscriptRow | TranscriptPreview, TranscriptItem], None] | None
        ) = None
        self.item: TranscriptItem | None = None
        self._estimated_height = 32
        self._preview: TranscriptPreview | None = None

    def bind(
        self,
        item: TranscriptItem,
        bind_row: Callable[[TranscriptRow | TranscriptPreview, TranscriptItem], None],
    ) -> None:
        """Keep the data available without building an editor for an offscreen item."""
        self.item = item
        self._bind_row = bind_row
        if self.row is not None:
            bind_row(self.row, item)
        elif self.get_child() is not None and self._preview is not None:
            bind_row(self._preview, item)
        self.queue_resize()

    def unbind(self) -> None:
        """Flush before the factory reuses the cell for another segment."""
        if self.row is not None:
            self.row.flush()
            row = self.row
            self.row = None
            self.set_child(None)
            self._release_row(row)
        self.set_child(None)
        if self._preview is not None:
            self._release_preview(self._preview)
            self._preview = None
        self.item = None

    def set_columns(self, *, speakers: bool, timestamps: bool, compact: bool) -> None:
        """Keep offscreen estimates consistent with metadata and compact layout."""
        meta_height = 56 if speakers and timestamps else 32 if speakers else 24 if timestamps else 0
        header = self.item is not None and self.item.segment is None
        self._estimated_height = 80 if header else max(32, meta_height) + (24 if compact else 0)

    def leave_editor(self) -> None:
        """Return a saved segment to reading mode without detaching reading widgets."""
        if self.row is None or self.item is None or self.item.segment is None:
            return
        row = self.row
        self.row = None
        self.set_child(None)
        self._release_row(row)

    def ensure_row(self) -> TranscriptRow:
        """Create the editor on allocation or an explicit keyboard focus request."""
        if self.row is None:
            self.row = self._create_row()
            self.set_child(self.row)
            if self.item is not None and self._bind_row is not None:
                self._bind_row(self.row, self.item)
        return self.row

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        child = self.get_child()
        if child is not None:
            return child.measure(orientation, for_size)
        height = self._estimated_height if orientation == Gtk.Orientation.VERTICAL else 0
        return height, height, -1, -1

    def prepare(self) -> Gtk.Widget | None:
        """Build the reading widget when its row is about to enter the viewport."""
        item = self.item
        if item is None:
            return None
        if self.row is not None or item.segment is None or item.position is None:
            child = self.ensure_row()
        else:
            if self._preview is None:
                self._preview = self._create_preview(self.ensure_row)
            child = self._preview
            if self.get_child() is not child:
                self.set_child(child)
                if self._bind_row is not None:
                    self._bind_row(child, item)
        return child

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        # Gtk.ListView measures ~200 rows but only allocates those near the viewport.
        child = self.prepare()
        if child is None:
            return
        child.measure(Gtk.Orientation.VERTICAL, width)
        child.allocate(width, height, baseline, None)
        minimum, _natural, _minimum_baseline, _natural_baseline = child.measure(
            Gtk.Orientation.VERTICAL, width
        )
        if minimum != height:
            # A resize queued during allocation is consumed by GTK's current pass.
            # Schedule it afterwards, or short transcripts retain estimated heights.
            GLib.idle_add(self._remeasure)

    def _remeasure(self) -> bool:
        self.queue_resize()
        return False
