"""The user's saved defaults: model, language and audio devices."""

from collections.abc import Callable
from dataclasses import replace

from kikoe.core.preferences import Preferences
from kikoe.storage.settings_writer import SettingsWriter


class PreferencesRepository:
    """Persists preference edits and tells listeners once they are committed."""

    def __init__(self, writer: SettingsWriter) -> None:
        self._writer = writer
        self._listeners: list[Callable[[], None]] = []

    @property
    def current(self) -> Preferences:
        """The persisted defaults, shared by all transcription entry points."""
        return self._writer.state.preferences

    def connect_changed(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after defaults are successfully committed and after loading.

        Returns a function that stops the calls.
        """
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def load(self) -> None:
        await self._writer.ensure_loaded()
        self._notify()

    async def update(self, change: Callable[[Preferences], Preferences]) -> None:
        """Merge a field edit with the latest snapshot inside the same writer lock."""
        await self._writer.update(
            lambda state: replace(state, preferences=change(state.preferences))
        )
        self._notify()

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()
