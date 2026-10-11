import json
from pathlib import Path

import pytest

from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Provider
from kikoe.storage.settings import SettingsError, SettingsStore

PROVIDER = Provider(
    "p1",
    "Groq",
    ApiKind.OPENAI_COMPATIBLE,
    "https://api.groq.com/openai/v1",
    (ModelSpec("whisper-large-v3", "Whisper “Large”", Capabilities(timestamps=True)),),
)


def test_missing_file_loads_as_no_providers(tmp_path: Path) -> None:
    assert SettingsStore(tmp_path / "settings.json").load() == ()


def test_saved_providers_load_back_unchanged(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save((PROVIDER,))
    assert store.load() == (PROVIDER,)


def test_saved_file_records_the_version_and_never_a_key(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    SettingsStore(path).save((PROVIDER,))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "key" not in path.read_text(encoding="utf-8").lower()


def test_save_leaves_no_temporary_file(tmp_path: Path) -> None:
    SettingsStore(tmp_path / "settings.json").save((PROVIDER,))
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_damaged_file_is_reported_not_ignored(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="damaged"):
        SettingsStore(path).load()


def test_newer_settings_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"version": 99, "providers": []}', encoding="utf-8")
    with pytest.raises(SettingsError, match="version"):
        SettingsStore(path).load()


def test_unknown_api_kind_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    entry = {"id": "p", "name": "X", "api": "bogus", "base_url": "https://x"}
    path.write_text(json.dumps({"version": 1, "providers": [entry]}), encoding="utf-8")
    with pytest.raises(SettingsError):
        SettingsStore(path).load()
