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
    assert data["version"] == 2
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


@pytest.mark.parametrize("api", list(ApiKind))
def test_all_official_and_compatible_api_types_round_trip(tmp_path: Path, api: ApiKind) -> None:
    from dataclasses import replace

    provider = replace(PROVIDER, api=api)
    store = SettingsStore(tmp_path / "settings.json")
    store.save((provider,))
    assert store.load() == (provider,)


def test_version_one_upgrades_without_losing_providers(tmp_path: Path) -> None:
    from kikoe.core.preferences import Preferences

    path = tmp_path / "settings.json"
    SettingsStore(path).save((PROVIDER,))
    data = json.loads(path.read_text())
    data["version"] = 1
    data.pop("preferences")
    path.write_text(json.dumps(data))
    state = SettingsStore(path).load_state()
    assert state.providers == (PROVIDER,)
    assert state.preferences == Preferences()


def test_defaults_survive_legacy_provider_save(tmp_path: Path) -> None:
    from kikoe.core.preferences import Preferences
    from kikoe.storage.settings import Settings

    store = SettingsStore(tmp_path / "settings.json")
    preferences = Preferences(("p1", "whisper-large-v3"), ("p1", "whisper-large-v3"), "zh-CN")
    store.save_state(Settings((PROVIDER,), preferences))
    store.save(())
    assert store.load_state() == Settings((), preferences)


def test_failed_atomic_replace_keeps_previous_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save((PROVIDER,))

    def fail_replace(self: Path, target: Path) -> Path:
        raise PermissionError("blocked")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(SettingsError, match="save"):
        store.save(())
    assert store.load() == (PROVIDER,)


@pytest.mark.parametrize(
    "preferences",
    [
        {"language": "en_US"},
        {"language": 42},
        {"default_model": ["p"]},
        {"recent_model": ["p", ""]},
        {"recent_model": "model"},
    ],
)
def test_malformed_defaults_are_reported(tmp_path: Path, preferences: dict[str, object]) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 2, "providers": [], "preferences": preferences}))
    with pytest.raises(SettingsError):
        SettingsStore(path).load_state()
