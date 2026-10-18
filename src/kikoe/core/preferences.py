"""User defaults and validation independent of the desktop toolkit."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from kikoe.core.models import ModelKey, Provider


@dataclass(frozen=True, slots=True)
class Preferences:
    """The recent model and audio device names survive restarts; no device means system default."""

    recent_model: ModelKey | None = None
    language: str | None = None
    input_device: str | None = None
    output_device: str | None = None


def recent_provider(preferences: Preferences, providers: Sequence[Provider]) -> Provider | None:
    """Find the last selected provider for startup prewarming."""
    if preferences.recent_model is None:
        return None
    return next((p for p in providers if p.provider_id == preferences.recent_model[0]), None)


MIN_WINDOW_WIDTH = 360
MIN_WINDOW_HEIGHT = 294
MAX_WINDOW_SIZE = 16384


@dataclass(frozen=True, slots=True)
class WindowState:
    """The size the main window had when it last changed, restored on the next launch."""

    width: int
    height: int
    maximized: bool = False


def valid_window_state(state: WindowState) -> bool:
    """A damaged value must not produce an unusable (tiny or absurdly large) window."""
    return (
        MIN_WINDOW_WIDTH <= state.width <= MAX_WINDOW_SIZE
        and MIN_WINDOW_HEIGHT <= state.height <= MAX_WINDOW_SIZE
    )


def valid_language(language: str) -> bool:
    """Accept common BCP 47 language tags without claiming provider support."""
    return bool(re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language))
