"""Read the model presets bundled with the application."""

import json
from importlib.resources import files

from kikoe.core.model_catalog import ModelCatalog, parse_catalog


def load_catalog() -> ModelCatalog:
    """Blocking; call it via ``asyncio.to_thread``. A broken file is a packaging bug."""
    text = files("kikoe.storage").joinpath("model_presets.json").read_text(encoding="utf-8")
    return parse_catalog(json.loads(text))
