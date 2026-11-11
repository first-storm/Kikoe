"""Failures written for the user reach them instead of a generic message."""

from pathlib import Path

import pytest

from kikoe.core.local_models import LocalSettings
from kikoe.services.errors import USER_FACING_ERRORS
from kikoe.storage.local_settings import LocalSettingsStore


def test_a_failed_local_settings_save_is_shown_to_the_user(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    store = LocalSettingsStore(blocker / "local.json")

    with pytest.raises(USER_FACING_ERRORS, match="Could not save the local model settings"):
        store.save(LocalSettings().with_device("engine", "cpu"))
