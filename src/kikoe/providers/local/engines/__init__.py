"""Registered engines. Removing an engine means deleting its folder and its line here.

Each engine is a package holding ``catalog.json`` (pinned downloads and models) and
``adapter.py`` (run inside the worker process). Packages are listed explicitly because
frozen builds can only ship modules that are named somewhere.
"""

import json
from importlib.resources import files

from kikoe.core.local_models import (
    EngineSpec,
    LocalCatalog,
    LocalCatalogError,
    LocalModelSpec,
    merge_catalogs,
)
from kikoe.core.local_models import parse_engine_catalog as _parse

ENGINES: dict[str, str] = {
    "transcribe-cpp": "kikoe.providers.local.engines.transcribe_cpp",
}
"""Engine ID → package. The ID is persisted in install folders and settings."""


def load_catalog(engines: dict[str, str] | None = None) -> LocalCatalog:
    """Blocking; call it via ``asyncio.to_thread``. A broken file is a packaging bug."""
    parts: list[tuple[EngineSpec, tuple[LocalModelSpec, ...]]] = []
    for engine_id, package in (engines if engines is not None else ENGINES).items():
        text = files(package).joinpath("catalog.json").read_text(encoding="utf-8")
        engine, models = _parse(json.loads(text))
        if engine.engine_id != engine_id:
            raise LocalCatalogError(f"{package} declares engine {engine.engine_id}")
        parts.append((engine, models))
    return merge_catalogs(parts)
