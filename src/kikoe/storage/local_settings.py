"""Local engine choices in their own file (``local.json``), apart from cloud settings.

Keeping them separate means removing the local engines touches nothing else, and a
damaged file only resets these choices: they are preferences, not data.
"""

import json
import logging
from pathlib import Path
from typing import cast

from kikoe.core.local_models import LocalSettings
from kikoe.i18n import _

log = logging.getLogger(__name__)

LOCAL_SETTINGS_VERSION = 1


class LocalSettingsError(Exception):
    """The file could not be saved; the message is shown to the user."""


class LocalSettingsStore:
    """Blocking; call it via ``asyncio.to_thread``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> LocalSettings:
        try:
            data: object = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return LocalSettings()
        except OSError, ValueError:
            log.warning("Ignoring unreadable local engine settings")
            return LocalSettings()
        if not isinstance(data, dict):
            return LocalSettings()
        top = cast(dict[str, object], data)
        if top.get("version") != LOCAL_SETTINGS_VERSION:
            log.warning("Ignoring local engine settings of another version")
            return LocalSettings()
        devices = top.get("devices", {})
        if not isinstance(devices, dict):
            return LocalSettings()
        pairs = cast(dict[object, object], devices).items()
        return LocalSettings(
            tuple(sorted((k, v) for k, v in pairs if isinstance(k, str) and isinstance(v, str)))
        )

    def save(self, settings: LocalSettings) -> None:
        temporary = self._path.with_name(self._path.name + ".tmp")
        data = {"version": LOCAL_SETTINGS_VERSION, "devices": dict(settings.devices)}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as error:
            raise LocalSettingsError(
                _("Could not save the local model settings: {error}").format(error=error)
            ) from error
