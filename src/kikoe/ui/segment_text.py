"""The text view that shows and edits one segment's text, sized for any width.

``Gtk.TextView`` answers every height request with the height of its last layout,
whatever width it is offered, so a row in a list first gets the wrong height and only
corrects itself after it has been drawn. This view lays its text out with Pango for the
offered width, line by line as the text view does, so rows are right in the first frame.
"""

from gi.repository import Gtk, Pango

from kikoe.ui.templates import ui_file

_CURSOR_SPACE = 1
"""Pixels the text view keeps free at the end of each line for the caret."""
_MEASURED_WIDTHS = 4
"""Heights kept per text; resizing the window asks about a new width every frame."""


@Gtk.Template(filename=ui_file("segment_text"))
class SegmentText(Gtk.TextView):
    __gtype_name__ = "KikoeSegmentText"

    def __init__(self) -> None:
        super().__init__()
        self._preedit = ""
        self._heights: dict[tuple[int, str], int] = {}
        """Heights of the current text by (width, font); width -1 is unlimited."""
        self.get_buffer().connect("changed", self._on_buffer_changed)
        self.connect("preedit-changed", self._on_preedit_changed)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        # The text view asks for a constant size, so GTK would never offer it a width.
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if orientation == Gtk.Orientation.VERTICAL:
            height = self._height_for(for_size)
            return height, height, -1, -1
        minimum, natural, _baseline, _natural_baseline = Gtk.TextView.do_measure(
            self, orientation, for_size
        )
        return minimum, natural, -1, -1

    def _height_for(self, width: int) -> int:
        """The height at ``width``; with no width, at unlimited width as for a label."""
        font = self.get_pango_context().get_font_description()
        key = width, font.to_string() if font is not None else ""
        height = self._heights.get(key)
        if height is not None:
            return height
        if len(self._heights) >= _MEASURED_WIDTHS:
            self._heights.clear()
        wrap_width = width - self.get_left_margin() - self.get_right_margin() - _CURSOR_SPACE
        height = self.get_top_margin() + self.get_bottom_margin()
        for paragraph in self._laid_out_text().split("\n"):
            layout = self.create_pango_layout(paragraph)
            layout.set_width(max(1, wrap_width) * Pango.SCALE if width >= 0 else -1)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            # Lines are rounded to pixels one by one, as the text view does.
            lines = layout.get_iter()
            while True:
                top, bottom = lines.get_line_yrange()
                height += _pixels(bottom) - _pixels(top)
                if not lines.next_line():
                    break
            height += self.get_pixels_inside_wrap() * (layout.get_line_count() - 1)
            height += self.get_pixels_above_lines() + self.get_pixels_below_lines()
        self._heights[key] = height
        return height

    def _laid_out_text(self) -> str:
        """The text, including what an input method is still composing at the caret."""
        buffer = self.get_buffer()
        start, end = buffer.get_bounds()
        text = buffer.get_text(start, end, False)
        if self._preedit:
            cursor = buffer.get_iter_at_mark(buffer.get_insert()).get_offset()
            text = text[:cursor] + self._preedit + text[cursor:]
        return text

    def _remeasure(self) -> None:
        self._heights.clear()
        self.queue_resize()

    def _on_buffer_changed(self, _buffer: Gtk.TextBuffer) -> None:
        self._remeasure()

    def _on_preedit_changed(self, _view: Gtk.TextView, preedit: str) -> None:
        self._preedit = preedit
        self._remeasure()


def _pixels(units: int) -> int:
    """Pango units to pixels, rounded like ``PANGO_PIXELS``."""
    return (units + Pango.SCALE // 2) // Pango.SCALE
