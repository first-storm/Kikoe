"""Engines and models that run on this device, described by bundled catalogs.

Each engine ships a catalog (``providers/local/engines/<engine>/catalog.json``) listing
its downloadable builds and models, every file pinned by SHA-256. This module validates
those documents and answers questions about them without touching the disk.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from urllib.parse import unquote, urlsplit

CATALOG_VERSION = 1

_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TARGET = re.compile(r"[a-z0-9_-]*")


class LocalCatalogError(ValueError):
    """A bundled engine catalog is malformed; a packaging bug, not a user error."""


class OperatingSystem(StrEnum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"


class Architecture(StrEnum):
    X86_64 = "x86_64"
    ARM64 = "arm64"


class Accelerator(StrEnum):
    """Hardware an engine build can run on; every build also runs on the CPU."""

    CPU = "cpu"
    VULKAN = "vulkan"
    CUDA = "cuda"
    METAL = "metal"


class ArchiveKind(StrEnum):
    """How a downloaded file becomes installed files."""

    FILE = "file"
    ZIP = "zip"
    TAR_GZ = "tar.gz"


class ModelRole(StrEnum):
    ASR = "asr"
    """Turns speech into text."""
    DIARIZE = "diarize"
    """Tells speakers apart; used together with an ASR model."""


@dataclass(frozen=True, slots=True)
class Platform:
    os: OperatingSystem
    arch: Architecture


class InstallStatus(StrEnum):
    MISSING = "missing"
    DOWNLOADING = "downloading"
    INSTALLED = "installed"
    BUNDLED = "bundled"
    """Shipped inside the application; it cannot be removed or replaced."""


@dataclass(frozen=True, slots=True)
class InstallState:
    status: InstallStatus = InstallStatus.MISSING
    received: int = 0
    """Bytes downloaded so far, while downloading."""
    total: int = 0


@dataclass(frozen=True, slots=True)
class Download:
    """One pinned file; archives are unpacked into ``target`` inside the install folder."""

    url: str
    sha256: str
    size: int
    archive: ArchiveKind = ArchiveKind.FILE
    target: str = ""
    """Sub-folder of the install folder; empty for its top level."""
    strip: int = 0
    """Leading folders removed from archive member paths, like ``tar --strip-components``."""

    @property
    def filename(self) -> str:
        """The last URL path segment, which names a plain file once installed."""
        return unquote(urlsplit(self.url).path.rsplit("/", 1)[-1])


@dataclass(frozen=True, slots=True)
class EngineBuild:
    """Native files for one platform and set of accelerators."""

    build_id: str
    platform: Platform
    accelerators: tuple[Accelerator, ...]
    components: tuple[Download, ...]


@dataclass(frozen=True, slots=True)
class EngineSpec:
    engine_id: str
    display_name: str
    version: str
    homepage: str
    components: tuple[Download, ...]
    """Files every build needs, such as the Python binding."""
    builds: tuple[EngineBuild, ...]

    def builds_for(self, platform: Platform) -> tuple[EngineBuild, ...]:
        return tuple(b for b in self.builds if b.platform == platform)

    def build(self, build_id: str) -> EngineBuild | None:
        return next((b for b in self.builds if b.build_id == build_id), None)

    def downloads(self, build: EngineBuild) -> tuple[Download, ...]:
        return (*self.components, *build.components)


@dataclass(frozen=True, slots=True)
class LocalModelSpec:
    model_id: str
    """Unique across all engines; also the ``ModelSpec.model_id`` of the local provider."""
    engine_id: str
    display_name: str
    family: str
    """Groups related sizes and versions on the preferences page."""
    role: ModelRole
    file: Download
    languages: tuple[str, ...]
    """Language codes; empty means many languages with automatic detection."""
    timestamps: bool = False
    streaming: bool = False
    """Text can appear while recording."""
    max_seconds: int | None = None
    """Longest audio one request may hold; longer files are split at pauses."""

    @property
    def size(self) -> int:
        return self.file.size


@dataclass(frozen=True, slots=True)
class LocalCatalog:
    engines: tuple[EngineSpec, ...] = ()
    models: tuple[LocalModelSpec, ...] = ()

    def engine(self, engine_id: str) -> EngineSpec | None:
        return next((e for e in self.engines if e.engine_id == engine_id), None)

    def model(self, model_id: str) -> LocalModelSpec | None:
        return next((m for m in self.models if m.model_id == model_id), None)

    def models_of(self, engine_id: str) -> tuple[LocalModelSpec, ...]:
        return tuple(m for m in self.models if m.engine_id == engine_id)


_RECOMMENDED = (Accelerator.METAL, Accelerator.VULKAN, Accelerator.CPU)
"""Preferred builds before anything is known about the GPU. CUDA is never the default:
its download is ten times larger and it only helps on NVIDIA cards, while Vulkan also
runs on them."""


def recommend_build(builds: Iterable[EngineBuild]) -> EngineBuild | None:
    """The build to offer first; the user may still pick another one."""
    candidates = tuple(builds)
    for accelerator in _RECOMMENDED:
        for build in candidates:
            if accelerator in build.accelerators and Accelerator.CUDA not in build.accelerators:
                return build
    return candidates[0] if candidates else None


def needs_device_access(build: EngineBuild, sandboxed: bool) -> bool:
    """Flatpak only exposes NVIDIA's devices after ``flatpak override --device=all``."""
    return sandboxed and Accelerator.CUDA in build.accelerators


def merge_catalogs(parts: Iterable[tuple[EngineSpec, tuple[LocalModelSpec, ...]]]) -> LocalCatalog:
    """Combine per-engine catalogs; model IDs must be unique across engines."""
    engines: list[EngineSpec] = []
    models: list[LocalModelSpec] = []
    for engine, engine_models in parts:
        if any(e.engine_id == engine.engine_id for e in engines):
            raise LocalCatalogError(f"duplicate engine {engine.engine_id}")
        engines.append(engine)
        models.extend(engine_models)
    _unique((m.model_id for m in models), "model")
    return LocalCatalog(tuple(engines), tuple(models))


def parse_engine_catalog(data: object) -> tuple[EngineSpec, tuple[LocalModelSpec, ...]]:
    """Validate one engine's catalog document up front so a bad edit fails in tests."""
    top = _object(data, "the catalog")
    if top.get("version") != CATALOG_VERSION:
        raise LocalCatalogError(f"unsupported catalog version {top.get('version')!r}")
    engine = _engine(_object(top.get("engine"), "engine"))
    models = tuple(
        _model(_object(entry, "a model"), engine.engine_id)
        for entry in _list(top.get("models", []), "models")
    )
    _unique((m.model_id for m in models), "model")
    return engine, models


def _engine(entry: Mapping[str, object]) -> EngineSpec:
    engine_id = _identifier(entry.get("id"), "engine id")
    builds = tuple(_build(_object(b, "a build")) for b in _list(entry.get("builds"), "builds"))
    if not builds:
        raise LocalCatalogError(f"engine {engine_id} has no builds")
    _unique((b.build_id for b in builds), "build")
    return EngineSpec(
        engine_id=engine_id,
        display_name=_string(entry.get("name"), "engine name"),
        version=_string(entry.get("version"), "engine version"),
        homepage=_https(entry.get("homepage"), "engine homepage"),
        components=_downloads(entry.get("components", [])),
        builds=builds,
    )


def _build(entry: Mapping[str, object]) -> EngineBuild:
    build_id = _identifier(entry.get("id"), "build id")
    accelerators = tuple(
        _enum(Accelerator, a, "accelerator") for a in _list(entry.get("accelerators"), "accel")
    )
    if Accelerator.CPU not in accelerators:
        raise LocalCatalogError(f"build {build_id} must also run on the CPU")
    components = _downloads(entry.get("components"))
    if not components:
        raise LocalCatalogError(f"build {build_id} has no files")
    return EngineBuild(
        build_id=build_id,
        platform=Platform(
            _enum(OperatingSystem, entry.get("os"), "os"),
            _enum(Architecture, entry.get("arch"), "arch"),
        ),
        accelerators=accelerators,
        components=components,
    )


def _model(entry: Mapping[str, object], engine_id: str) -> LocalModelSpec:
    model_id = _identifier(entry.get("id"), "model id")
    file = _download(_object(entry.get("file"), f"file of {model_id}"))
    if file.archive is not ArchiveKind.FILE:
        raise LocalCatalogError(f"model {model_id} must be a single file")
    max_seconds = entry.get("max_seconds")
    if max_seconds is not None and (type(max_seconds) is not int or max_seconds <= 0):
        raise LocalCatalogError(f"model {model_id} has an invalid max_seconds")
    return LocalModelSpec(
        model_id=model_id,
        engine_id=engine_id,
        display_name=_string(entry.get("name"), "model name"),
        family=_string(entry.get("family"), "model family"),
        role=_enum(ModelRole, entry.get("role", ModelRole.ASR.value), "role"),
        file=file,
        languages=tuple(_string(c, "a language") for c in _list(entry.get("languages", []), "l")),
        timestamps=_flag(entry, "timestamps"),
        streaming=_flag(entry, "streaming"),
        max_seconds=max_seconds,
    )


def _downloads(value: object) -> tuple[Download, ...]:
    files = tuple(_download(_object(f, "a file")) for f in _list(value, "files"))
    _unique((f"{f.target}/{f.filename}" for f in files), "file")
    return files


def _download(entry: Mapping[str, object]) -> Download:
    url = _https(entry.get("url"), "file url")
    sha256 = entry.get("sha256")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise LocalCatalogError(f"{url} needs a lowercase SHA-256")
    size = entry.get("size")
    if type(size) is not int or size <= 0:
        raise LocalCatalogError(f"{url} needs a positive size")
    target = entry.get("target", "")
    if not isinstance(target, str) or not _TARGET.fullmatch(target):
        raise LocalCatalogError(f"{url} has an invalid target folder")
    strip = entry.get("strip", 0)
    if type(strip) is not int or strip < 0:
        raise LocalCatalogError(f"{url} has an invalid strip count")
    download = Download(
        url,
        sha256,
        size,
        _enum(ArchiveKind, entry.get("archive", "file"), "archive"),
        target,
        strip,
    )
    name = download.filename
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise LocalCatalogError(f"{url} does not end in a file name")
    return download


def _https(value: object, what: str) -> str:
    text = _string(value, what)
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.hostname:
        raise LocalCatalogError(f"{what} must be an https URL: {text}")
    return text


def _flag(entry: Mapping[str, object], key: str) -> bool:
    value = entry.get(key, False)
    if not isinstance(value, bool):
        raise LocalCatalogError(f"{key} must be true or false")
    return value


def _enum[E: StrEnum](kind: type[E], value: object, what: str) -> E:
    try:
        return kind(_string(value, what))
    except ValueError as error:
        raise LocalCatalogError(f"unknown {what} {value!r}") from error


def _identifier(value: object, what: str) -> str:
    text = _string(value, what)
    if not _ID.fullmatch(text):
        raise LocalCatalogError(f"invalid {what} {text!r}")
    return text


def _unique(values: Iterable[str], what: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise LocalCatalogError(f"duplicate {what} {value}")
        seen.add(value)


def _object(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LocalCatalogError(f"{what} must be an object")
    return cast(dict[str, object], value)


def _list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise LocalCatalogError(f"{what} must be a list")
    return cast(list[object], value)


def _string(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise LocalCatalogError(f"{what} must be a non-empty string")
    return value
