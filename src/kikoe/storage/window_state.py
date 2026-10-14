"""The main window's remembered size."""

from dataclasses import replace

from kikoe.core.preferences import WindowState
from kikoe.storage.settings_writer import SettingsWriter


class WindowStateStore:
    """Saves the window geometry; nothing needs to hear about it, so there are no listeners."""

    def __init__(self, writer: SettingsWriter) -> None:
        self._writer = writer

    @property
    def window(self) -> WindowState | None:
        return self._writer.state.window

    async def update(self, window: WindowState) -> None:
        await self._writer.update(lambda state: replace(state, window=window))
