"""Importing the app's modules must not load the provider SDKs or the keyring.

They take hundreds of milliseconds to import and are only needed once a transcription
starts, so loading them at startup delays the first window.
"""

import subprocess
import sys

import pytest

HEAVY_MODULES = (
    "openai",
    "groq",
    "mistralai",
    "google.genai",
    "openrouter",
    "deepgram",
    "keyring",
)

LIGHT_MODULES = (
    "kikoe.providers.errors",
    "kikoe.providers.factory",
    "kikoe.services.realtime",
    "kikoe.services.recording_workflow",
    "kikoe.services.file_transcription",
    "kikoe.storage.secrets",
)

CHECK = """
import importlib, sys
importlib.import_module(sys.argv[1])
loaded = sorted(
    name for name in sys.argv[2:] if any(m == name or m.startswith(name + ".") for m in sys.modules)
)
print(",".join(loaded))
"""


@pytest.mark.parametrize("module", LIGHT_MODULES)
def test_importing_a_module_does_not_load_sdks_or_keyring(module: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHECK, module, *HEAVY_MODULES],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == ""
