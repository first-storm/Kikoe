"""Bundled per-model defaults and hard limits, so capability rules live in data, not code.

The catalog is a JSON document (``storage/model_presets.json``). Each API has a fallback
entry; model rules refine it for known model IDs. Every switch is ``on`` (default
enabled), ``off`` (default disabled, but the user may enable it) or ``unsupported``
(always off, and the model dialog disables it).
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import cast

from kikoe.core.models import ApiKind, Capabilities, ModeCapabilities, ModelSpec

CATALOG_VERSION = 1


class CatalogError(ValueError):
    """The bundled preset file is malformed; a packaging bug, not a user error."""


class Support(StrEnum):
    ON = "on"
    OFF = "off"
    UNSUPPORTED = "unsupported"

    @property
    def allowed(self) -> bool:
        return self is not Support.UNSUPPORTED


_FILE_FIELDS = ("available", "timestamps", "diarization", "streaming")
_REALTIME_FIELDS = ("available", "timestamps", "diarization")
"""Streaming describes uploads; live text never has it."""


@dataclass(frozen=True, slots=True)
class ModePreset:
    """Defaults and limits for one request mode."""

    available: Support = Support.OFF
    timestamps: Support = Support.OFF
    diarization: Support = Support.OFF
    streaming: Support = Support.UNSUPPORTED

    def defaults(self) -> ModeCapabilities | None:
        if self.available is not Support.ON:
            return None
        return ModeCapabilities(
            timestamps=self.timestamps is Support.ON,
            diarization=self.diarization is Support.ON,
            streaming=self.streaming is Support.ON,
        )

    def clamp(self, capabilities: ModeCapabilities | None) -> ModeCapabilities | None:
        if capabilities is None or not self.available.allowed:
            return None
        return ModeCapabilities(
            timestamps=capabilities.timestamps and self.timestamps.allowed,
            diarization=capabilities.diarization and self.diarization.allowed,
            streaming=capabilities.streaming and self.streaming.allowed,
        )


@dataclass(frozen=True, slots=True)
class ModelPreset:
    """Both modes of one model; the defaults always enable at least one of them."""

    file: ModePreset
    realtime: ModePreset

    def defaults(self) -> Capabilities:
        return Capabilities(file=self.file.defaults(), realtime=self.realtime.defaults())

    def clamp(self, capabilities: Capabilities) -> Capabilities:
        """Remove what the endpoint rejects; a model left with no mode gets the defaults."""
        clamped = Capabilities(
            file=self.file.clamp(capabilities.file),
            realtime=self.realtime.clamp(capabilities.realtime),
        )
        if clamped.file is None and clamped.realtime is None:
            return self.defaults()
        return clamped


type _Overrides = tuple[tuple[str, Support], ...]


@dataclass(frozen=True, slots=True)
class _Rule:
    apis: frozenset[ApiKind]
    ids: frozenset[str]
    prefixes: tuple[str, ...]
    file: _Overrides
    realtime: _Overrides

    def matches(self, api: ApiKind, model_id: str) -> bool:
        return api in self.apis and (model_id in self.ids or model_id.startswith(self.prefixes))

    def apply(self, base: ModelPreset) -> ModelPreset:
        return ModelPreset(
            file=replace(base.file, **dict(self.file)),
            realtime=replace(base.realtime, **dict(self.realtime)),
        )


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """Look up the preset for a model; unknown IDs get their API's fallback."""

    apis: Mapping[ApiKind, ModelPreset]
    rules: tuple[_Rule, ...] = ()

    def preset(self, api: ApiKind, model_id: str) -> ModelPreset:
        base = self.apis[api]
        rule = next((r for r in self.rules if r.matches(api, model_id)), None)
        return rule.apply(base) if rule else base

    def clamp(self, api: ApiKind, model: ModelSpec) -> ModelSpec:
        capabilities = self.preset(api, model.model_id).clamp(model.capabilities)
        return (
            model
            if capabilities == model.capabilities
            else replace(model, capabilities=capabilities)
        )


def parse_catalog(data: object) -> ModelCatalog:
    """Validate the whole document up front so a bad edit fails in tests, not at runtime."""
    top = _object(data, "the catalog")
    if top.get("version") != CATALOG_VERSION:
        raise CatalogError(f"unsupported catalog version {top.get('version')!r}")
    api_entries = _object(top.get("apis"), "apis")
    apis: dict[ApiKind, ModelPreset] = {}
    for api in ApiKind:
        entry = _object(api_entries.get(api.value), f"apis.{api.value}")
        apis[api] = ModelPreset(
            file=replace(ModePreset(), **dict(_overrides(entry, "file", _FILE_FIELDS))),
            realtime=replace(ModePreset(), **dict(_overrides(entry, "realtime", _REALTIME_FIELDS))),
        )
    rules = tuple(_rule(entry) for entry in _list(top.get("models", []), "models"))
    catalog = ModelCatalog(apis, rules)
    for api, preset in apis.items():
        _check_usable(preset, api.value)
    for index, rule in enumerate(rules):
        for api in rule.apis:
            _check_usable(rule.apply(apis[api]), f"models[{index}] for {api.value}")
    return catalog


def _rule(value: object) -> _Rule:
    entry = _object(value, "a model rule")
    try:
        apis = frozenset(ApiKind(_string(a, "an API")) for a in _list(entry.get("apis"), "apis"))
    except ValueError as error:
        raise CatalogError(str(error)) from error
    ids = frozenset(_string(i, "a model ID") for i in _list(entry.get("ids", []), "ids"))
    prefixes = tuple(_string(p, "a prefix") for p in _list(entry.get("prefixes", []), "prefixes"))
    if not apis or not (ids or prefixes):
        raise CatalogError("a model rule needs APIs and model IDs or prefixes")
    return _Rule(
        apis,
        ids,
        prefixes,
        _overrides(entry, "file", _FILE_FIELDS),
        _overrides(entry, "realtime", _REALTIME_FIELDS),
    )


def _overrides(entry: dict[str, object], mode: str, fields: tuple[str, ...]) -> _Overrides:
    values = _object(entry.get(mode, {}), mode)
    unknown = set(values) - set(fields)
    if unknown:
        raise CatalogError(f"unknown {mode} fields: {', '.join(sorted(unknown))}")
    try:
        return tuple((name, Support(value)) for name, value in values.items())
    except ValueError as error:
        raise CatalogError(str(error)) from error


def _check_usable(preset: ModelPreset, where: str) -> None:
    """Defaults are the fallback for a model left with no mode, so they must enable one."""
    if Support.ON not in (preset.file.available, preset.realtime.available):
        raise CatalogError(f"{where} enables neither file nor realtime transcription by default")


def _object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogError(f"{what} must be an object")
    return cast(dict[str, object], value)


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise CatalogError(f"{what} must be a list")
    return cast(list[object], value)


def _string(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise CatalogError(f"{what} must be a non-empty string")
    return value
