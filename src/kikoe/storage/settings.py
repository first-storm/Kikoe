"""Settings file: the list of providers and their models, as versioned JSON.

API keys are never written here; they live in the OS keyring (see ``secrets``).
"""

import json
import logging
from pathlib import Path
from typing import cast

from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Provider
from kikoe.paths import config_dir

log = logging.getLogger(__name__)

SETTINGS_VERSION = 1


class SettingsError(Exception):
    """The settings file cannot be read or written. The message is meant for the user."""


def default_settings_path() -> Path:
    return config_dir() / "settings.json"


class SettingsStore:
    """Reads and writes the settings file. Blocking; call it via ``asyncio.to_thread``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> tuple[Provider, ...]:
        """Return the saved providers; a missing file means a first run."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ()
        except OSError as error:
            raise SettingsError(f"Could not read the settings file: {error}") from error
        try:
            return _decode(json.loads(text))
        except (ValueError, KeyError, TypeError) as error:
            raise SettingsError(
                f"The settings file {self._path} is damaged or not understood: {error}"
            ) from error

    def save(self, providers: tuple[Provider, ...]) -> None:
        """Write atomically so a crash cannot leave a half-written file."""
        temporary = self._path.with_name(self._path.name + ".tmp")
        data = {"version": SETTINGS_VERSION, "providers": [_encode(p) for p in providers]}
        try:
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as error:
            raise SettingsError(f"Could not save the settings file: {error}") from error
        log.info("Saved %d providers to %s", len(providers), self._path)


def _encode(provider: Provider) -> dict[str, object]:
    return {
        "id": provider.provider_id,
        "name": provider.name,
        "api": provider.api.value,
        "base_url": provider.base_url,
        "models": [
            {
                "id": model.model_id,
                "name": model.display_name,
                "realtime": model.capabilities.realtime,
                "timestamps": model.capabilities.timestamps,
                "diarization": model.capabilities.diarization,
            }
            for model in provider.models
        ],
    }


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{what} must be an object")
    return cast(dict[str, object], value)


def _as_list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{what} must be a list")
    return cast(list[object], value)


def _decode(data: object) -> tuple[Provider, ...]:
    top = _as_dict(data, "the top level")
    version = top.get("version")
    if not isinstance(version, int) or version > SETTINGS_VERSION:
        raise ValueError(f"unsupported settings version {version!r}")
    return tuple(
        _decode_provider(entry) for entry in _as_list(top.get("providers", []), "providers")
    )


def _decode_provider(entry: object) -> Provider:
    fields = _as_dict(entry, "a provider")
    models = _as_list(fields.get("models", []), "models")
    return Provider(
        provider_id=str(fields["id"]),
        name=str(fields["name"]),
        api=ApiKind(fields["api"]),
        base_url=str(fields["base_url"]),
        models=tuple(_decode_model(model) for model in models),
    )


def _decode_model(entry: object) -> ModelSpec:
    fields = _as_dict(entry, "a model")
    return ModelSpec(
        model_id=str(fields["id"]),
        display_name=str(fields["name"]),
        capabilities=Capabilities(
            realtime=bool(fields.get("realtime", False)),
            timestamps=bool(fields.get("timestamps", False)),
            diarization=bool(fields.get("diarization", False)),
        ),
    )
