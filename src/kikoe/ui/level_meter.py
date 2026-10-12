"""A short history of actual microphone energy, matching the design's 28 bars."""

from collections import deque

import cairo
from gi.repository import Gtk

from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("level_meter"))
class LevelMeter(Gtk.DrawingArea):
    """Paint bars in the inherited theme color without allocating widgets per sample."""

    __gtype_name__ = "KikoeLevelMeter"

    def __init__(self) -> None:
        super().__init__()
        self._levels: deque[float] = deque([0.0] * 28, maxlen=28)
        self._announced = 0
        self.set_draw_func(self._draw)

    def set_level(self, level: float) -> None:
        self._levels.append(level)
        self._announce(level)
        self.queue_draw()

    def clear(self) -> None:
        self._levels = deque([0.0] * 28, maxlen=28)
        self._announce(0.0)
        self.queue_draw()

    def _announce(self, level: float) -> None:
        """Tell assistive technology the level, but only in coarse steps to avoid chatter."""
        percent = round(min(max(level, 0.0), 1.0) * 10) * 10
        if percent == self._announced:
            return
        self._announced = percent
        self.update_property([Gtk.AccessibleProperty.VALUE_NOW], [float(percent)])

    def _draw(
        self, _area: Gtk.DrawingArea, context: cairo.Context[cairo.Surface], width: int, height: int
    ) -> None:
        color = self.get_color()
        context.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        spacing = width / 28
        for index, level in enumerate(self._levels):
            bar_height = max(4, level * height)
            context.rectangle(index * spacing, (height - bar_height) / 2, 3, bar_height)
        context.fill()
