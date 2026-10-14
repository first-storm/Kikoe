"""Where the window controls sit, so headers can keep the app's buttons out of their way."""

from dataclasses import dataclass

_WINDOW_BUTTONS = frozenset({"close", "minimize", "maximize"})
"""Layout entries drawn as window controls; ``icon`` and ``menu`` are not counted."""


def _entries(side: str) -> tuple[str, ...]:
    return tuple(name for part in side.split(",") if (name := part.strip()))


@dataclass(frozen=True, slots=True)
class DecorationLayout:
    """A parsed ``gtk-decoration-layout`` value, e.g. ``close,minimize,maximize:``."""

    start: tuple[str, ...]
    end: tuple[str, ...]

    @classmethod
    def parse(cls, layout: str) -> DecorationLayout:
        """Without a colon GTK puts everything at the start, so this does too."""
        start, _, end = layout.partition(":")
        return cls(_entries(start), _entries(end))

    @property
    def controls_at_start(self) -> bool:
        """True on macOS-style layouts, where the start side would otherwise get crowded."""
        return _count_controls(self.start) > _count_controls(self.end)

    def close_only(self) -> str:
        """The layout reduced to Close, kept on the side the system put it."""
        if "close" in self.start:
            return "close:"
        if "close" in self.end:
            return ":close"
        return ":"


def _count_controls(side: tuple[str, ...]) -> int:
    return sum(name in _WINDOW_BUTTONS for name in side)
