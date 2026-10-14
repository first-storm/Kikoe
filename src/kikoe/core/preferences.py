"""User defaults and validation independent of the desktop toolkit."""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from kikoe.core.models import ModelKey, Provider


@dataclass(frozen=True, slots=True)
class Preferences:
    """Model identities and audio device names survive restarts; no device means system default."""

    default_model: ModelKey | None = None
    recent_model: ModelKey | None = None
    language: str | None = None
    input_device: str | None = None
    output_device: str | None = None


def default_provider(preferences: Preferences, providers: Sequence[Provider]) -> Provider | None:
    """The provider the next transcription most likely uses: the default model's, else the last.

    A removed provider counts as absent, so a stale preference never selects anything.
    """
    for key in (preferences.default_model, preferences.recent_model):
        if key is None:
            continue
        provider = next((p for p in providers if p.provider_id == key[0]), None)
        if provider is not None:
            return provider
    return None


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
