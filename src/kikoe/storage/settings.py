"""Settings file: the list of providers and their models, as versioned JSON.

API keys are never written here; they live in the OS keyring (see ``secrets``).
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from kikoe.core.models import (
    ApiKind,
    AudioFormat,
    Capabilities,
    LlmFormat,
    LlmSettings,
    ModeCapabilities,
    ModelKey,
    ModelSpec,
    Provider,
    UploadSettings,
)
from kikoe.core.preferences import (
    Preferences,
    WindowState,
    valid_context_bias,
    valid_language,
    valid_realtime_delay,
    valid_window_state,
)
from kikoe.core.provider_registry import descriptor
from kikoe.core.upload import validate_upload
from kikoe.i18n import _
from kikoe.paths import config_dir

log = logging.getLogger(__name__)

SETTINGS_VERSION = 2


@dataclass(frozen=True, slots=True)
class Settings:
    """One snapshot prevents provider edits from overwriting general preferences."""

    providers: tuple[Provider, ...] = ()
    preferences: Preferences = field(default_factory=Preferences)
    window: WindowState | None = None


class SettingsError(Exception):
    """The settings file cannot be read or written. The message is meant for the user."""


def default_settings_path() -> Path:
    return config_dir() / "settings.json"


class SettingsStore:
    """Reads and writes the settings file. Blocking; call it via ``asyncio.to_thread``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_state(self) -> Settings:
        """Upgrade version-one provider files with default preferences; missing means first run."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        except OSError as error:
            raise SettingsError(
                _("Could not read the settings file: {error}").format(error=error)
            ) from error
        try:
            data = json.loads(text)
            top = _as_dict(data, "the top level")
            return Settings(
                _decode(top),
                _decode_preferences(top.get("preferences", {})),
                _decode_window(top.get("window")),
            )
        except (ValueError, KeyError, TypeError) as error:
            raise SettingsError(
                _("The settings file {detail} is damaged or not understood: {error}").format(
                    detail=self._path, error=error
                )
            ) from error

    def save_state(self, settings: Settings) -> None:
        """Write the complete snapshot atomically so a crash cannot leave a half-written file."""
        temporary = self._path.with_name(self._path.name + ".tmp")
        data = {
            "version": SETTINGS_VERSION,
            "providers": [_encode(p) for p in settings.providers],
            "preferences": {
                "recent_model": settings.preferences.recent_model,
                "language": settings.preferences.language,
                "context_bias": list(settings.preferences.context_bias),
                "realtime_delay_ms": settings.preferences.realtime_delay_ms,
                "input_device": settings.preferences.input_device,
                "output_device": settings.preferences.output_device,
            },
            "window": _encode_window(settings.window),
        }
        try:
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self._path)
        except OSError as error:
            raise SettingsError(
                _("Could not save the settings file: {error}").format(error=error)
            ) from error
        log.info("Saved %d providers to %s", len(settings.providers), self._path)


def _encode(provider: Provider) -> dict[str, object]:
    validate_upload(provider.upload, provider.api)
    llm: dict[str, object] = {}
    if descriptor(provider.api).prompted:
        llm = {"llm": {"format": provider.llm.format.value, "prompt": provider.llm.prompt}}
    return {
        "id": provider.provider_id,
        "name": provider.name,
        "api": provider.api.value,
        "base_url": provider.base_url,
        "upload": {
            "parallelism": provider.upload.parallelism,
            "chunk_seconds": provider.upload.chunk_seconds,
            "chunk_bytes": provider.upload.chunk_bytes,
            "format": provider.upload.format,
        },
        "models": [
            {
                "id": model.model_id,
                "name": model.display_name,
                "file": _encode_mode(model.capabilities.file),
                "realtime": _encode_mode(model.capabilities.realtime),
            }
            for model in provider.models
        ],
        **llm,
    }


def _encode_mode(mode: ModeCapabilities | None) -> dict[str, bool] | None:
    if mode is None:
        return None
    return {
        "timestamps": mode.timestamps,
        "diarization": mode.diarization,
        "streaming": mode.streaming,
    }


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{what} must be an object")
    return cast(dict[str, object], value)


def _as_list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{what} must be a list")
    return cast(list[object], value)


def _decode(top: dict[str, object]) -> tuple[Provider, ...]:
    version = top.get("version")
    if type(version) is not int or not 1 <= version <= SETTINGS_VERSION:
        raise ValueError(f"unsupported settings version {version!r}")
    return tuple(
        _decode_provider(entry) for entry in _as_list(top.get("providers", []), "providers")
    )


def _decode_provider(entry: object) -> Provider:
    fields = _as_dict(entry, "a provider")
    models = _as_list(fields.get("models", []), "models")
    api = ApiKind(fields["api"])
    if not descriptor(api).configurable:
        raise ValueError(f"{api.value} providers are built in, not stored")
    return Provider(
        provider_id=str(fields["id"]),
        name=str(fields["name"]),
        api=api,
        base_url=str(fields["base_url"]),
        models=tuple(_decode_model(model) for model in models),
        upload=_decode_upload(fields.get("upload", {}), api),
        llm=_decode_llm(fields.get("llm", {})),
    )


def _decode_llm(value: object) -> LlmSettings:
    fields = _as_dict(value, "LLM settings")
    prompt = fields.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    return LlmSettings(LlmFormat(fields.get("format", LlmFormat.OPENAI)), prompt)


def _decode_upload(value: object, api: ApiKind) -> UploadSettings:
    fields = _as_dict(value, "upload settings")
    parallelism = fields.get("parallelism", 2)
    seconds = fields.get("chunk_seconds")
    size = fields.get("chunk_bytes")
    format_value = fields.get("format")
    if not isinstance(parallelism, int):
        raise ValueError("parallelism must be an integer")
    if seconds is not None and not isinstance(seconds, int):
        raise ValueError("chunk duration must be an integer")
    if size is not None and not isinstance(size, int):
        raise ValueError("chunk size must be an integer")
    settings = UploadSettings(
        parallelism, seconds, size, AudioFormat(format_value) if format_value is not None else None
    )
    validate_upload(settings, api)
    return settings


def _decode_model(entry: object) -> ModelSpec:
    fields = _as_dict(entry, "a model")
    return ModelSpec(
        model_id=str(fields["id"]),
        display_name=str(fields["name"]),
        capabilities=Capabilities(
            file=_decode_mode(fields.get("file")), realtime=_decode_mode(fields.get("realtime"))
        ),
    )


def _decode_mode(value: object) -> ModeCapabilities | None:
    if value is None:
        return None
    fields = _as_dict(value, "model capabilities")
    return ModeCapabilities(
        timestamps=bool(fields.get("timestamps", False)),
        diarization=bool(fields.get("diarization", False)),
        streaming=bool(fields.get("streaming", False)),
    )


def _decode_context_bias(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("invalid context bias")
    items = cast(list[object], value)
    terms = tuple(term for term in items if isinstance(term, str))
    if len(terms) != len(items) or not valid_context_bias(terms):
        raise ValueError("invalid context bias")
    return terms


def _decode_realtime_delay(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not valid_realtime_delay(value):
        raise ValueError("invalid realtime delay")
    return value


def _decode_preferences(value: object) -> Preferences:
    fields = _as_dict(value, "preferences")
    language = fields.get("language")
    if language is not None and (not isinstance(language, str) or not valid_language(language)):
        raise ValueError("invalid transcription language")
    return Preferences(
        recent_model=_decode_model_key(fields.get("recent_model")),
        language=language,
        context_bias=_decode_context_bias(fields.get("context_bias")),
        realtime_delay_ms=_decode_realtime_delay(fields.get("realtime_delay_ms")),
        input_device=_decode_device(fields.get("input_device")),
        output_device=_decode_device(fields.get("output_device")),
    )


def _encode_window(window: WindowState | None) -> dict[str, object] | None:
    if window is None:
        return None
    return {"width": window.width, "height": window.height, "maximized": window.maximized}


def _decode_window(value: object) -> WindowState | None:
    """Window geometry is a convenience, so anything unusable means the default size."""
    if not isinstance(value, dict):
        return None
    fields = cast(dict[str, object], value)
    width, height, maximized = fields.get("width"), fields.get("height"), fields.get("maximized")
    if not (isinstance(width, int) and isinstance(height, int) and isinstance(maximized, bool)):
        return None
    state = WindowState(width, height, maximized)
    return state if valid_window_state(state) else None


def _decode_device(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("an audio device must be a name")
    return value


def _decode_model_key(value: object) -> ModelKey | None:
    if value is None:
        return None
    parts = _as_list(value, "a model identity")
    if len(parts) != 2 or not all(isinstance(part, str) and part for part in parts):
        raise ValueError("a model identity must contain a provider ID and model ID")
    return cast(ModelKey, tuple(parts))
