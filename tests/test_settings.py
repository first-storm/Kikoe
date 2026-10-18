import json
from pathlib import Path

import pytest

from helpers import file_caps
from kikoe.core.models import (
    ApiKind,
    Capabilities,
    LlmFormat,
    LlmSettings,
    ModeCapabilities,
    ModelSpec,
    Provider,
)
from kikoe.core.preferences import WindowState
from kikoe.core.provider_registry import configurable_apis
from kikoe.storage.settings import Settings, SettingsError, SettingsStore

PROVIDER = Provider(
    "p1",
    "Groq",
    ApiKind.OPENAI_COMPATIBLE,
    "https://api.groq.com/openai/v1",
    (ModelSpec("whisper-large-v3", "Whisper “Large”", file_caps(timestamps=True)),),
)


def test_missing_file_loads_as_no_providers(tmp_path: Path) -> None:
    assert SettingsStore(tmp_path / "settings.json").load_state().providers == ()


def test_saved_providers_load_back_unchanged(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((PROVIDER,)))
    assert store.load_state().providers == (PROVIDER,)


def test_saved_file_records_the_version_and_never_a_key(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    SettingsStore(path).save_state(Settings((PROVIDER,)))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert "key" not in path.read_text(encoding="utf-8").lower()


def test_save_leaves_no_temporary_file(tmp_path: Path) -> None:
    SettingsStore(tmp_path / "settings.json").save_state(Settings((PROVIDER,)))
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_damaged_file_is_reported_not_ignored(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SettingsError, match="damaged"):
        SettingsStore(path).load_state()


def test_newer_settings_version_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"version": 99, "providers": []}', encoding="utf-8")
    with pytest.raises(SettingsError, match="version"):
        SettingsStore(path).load_state()


def test_unknown_api_kind_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    entry = {"id": "p", "name": "X", "api": "bogus", "base_url": "https://x"}
    path.write_text(json.dumps({"version": 1, "providers": [entry]}), encoding="utf-8")
    with pytest.raises(SettingsError):
        SettingsStore(path).load_state()


@pytest.mark.parametrize("api", configurable_apis())
def test_all_official_and_compatible_api_types_round_trip(tmp_path: Path, api: ApiKind) -> None:
    from dataclasses import replace

    provider = replace(PROVIDER, api=api)
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((provider,)))
    assert store.load_state().providers == (provider,)


def test_a_stored_built_in_provider_is_refused(tmp_path: Path) -> None:
    from dataclasses import replace

    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((replace(PROVIDER, api=ApiKind.LOCAL),)))
    with pytest.raises(SettingsError):
        store.load_state()


def test_version_one_upgrades_without_losing_providers(tmp_path: Path) -> None:
    from kikoe.core.preferences import Preferences

    path = tmp_path / "settings.json"
    SettingsStore(path).save_state(Settings((PROVIDER,)))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = 1
    data.pop("preferences")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    state = SettingsStore(path).load_state()
    assert state.providers == (PROVIDER,)
    assert state.preferences == Preferences()


def test_per_mode_capabilities_round_trip(tmp_path: Path) -> None:
    live = ModelSpec("live", "Live", Capabilities(file=None, realtime=ModeCapabilities(True)))
    both = ModelSpec(
        "both",
        "Both",
        Capabilities(
            file=ModeCapabilities(True, True, True),
            realtime=ModeCapabilities(diarization=True),
        ),
    )
    provider = Provider("p", "P", ApiKind.DEEPGRAM, "https://example.test", (live, both))
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((provider,)))
    assert store.load_state().providers == (provider,)


def test_failed_atomic_replace_keeps_previous_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((PROVIDER,)))

    def fail_replace(self: Path, target: Path) -> Path:
        raise PermissionError("blocked")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(SettingsError, match="save"):
        store.save_state(Settings())
    assert store.load_state().providers == (PROVIDER,)


@pytest.mark.parametrize(
    "preferences",
    [
        {"language": "en_US"},
        {"language": 42},
        {"recent_model": ["p", ""]},
        {"recent_model": "model"},
    ],
)
def test_malformed_defaults_are_reported(tmp_path: Path, preferences: dict[str, object]) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": 2, "providers": [], "preferences": preferences}),
        encoding="utf-8",
    )
    with pytest.raises(SettingsError):
        SettingsStore(path).load_state()


def test_obsolete_default_model_is_ignored_and_removed_on_save(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "providers": [],
                "preferences": {"default_model": ["obsolete"], "recent_model": ["p", "m"]},
            }
        )
    )
    store = SettingsStore(path)
    settings = store.load_state()
    assert settings.preferences.recent_model == ("p", "m")
    store.save_state(settings)
    assert "default_model" not in json.loads(path.read_text())["preferences"]


def test_audio_devices_round_trip_and_default_to_system(tmp_path: Path) -> None:
    from dataclasses import replace

    from kikoe.core.preferences import Preferences
    from kikoe.storage.settings import Settings

    store = SettingsStore(tmp_path / "settings.json")
    assert store.load_state().preferences == Preferences()
    preferences = replace(Preferences(), input_device="USB Mic", output_device="Headset")
    store.save_state(Settings((), preferences))
    assert store.load_state().preferences == preferences


@pytest.mark.parametrize("value", ["", 3, ["x"]])
def test_malformed_audio_device_is_reported(tmp_path: Path, value: object) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": 2, "providers": [], "preferences": {"input_device": value}}),
        encoding="utf-8",
    )
    with pytest.raises(SettingsError):
        SettingsStore(path).load_state()


def test_window_state_round_trips(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    window = WindowState(900, 600, maximized=True)
    store.save_state(Settings((PROVIDER,), window=window))

    assert store.load_state().window == window


def test_missing_window_state_loads_as_none(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((PROVIDER,)))
    assert store.load_state().window is None


@pytest.mark.parametrize(
    "window",
    [
        {"width": 10, "height": 600, "maximized": False},
        {"width": 900, "height": 10**9, "maximized": False},
        {"width": "wide", "height": 600, "maximized": False},
        {"width": 900, "height": 600, "maximized": "yes"},
        {"width": 900},
        "big",
    ],
)
def test_unusable_window_state_falls_back_to_the_default_size(
    tmp_path: Path, window: object
) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 2, "providers": [], "window": window}), encoding="utf-8")

    assert SettingsStore(path).load_state().window is None


def test_provider_upload_settings_survive_restart_and_legacy_files_use_defaults(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from kikoe.core.models import AudioFormat, UploadSettings

    store = SettingsStore(tmp_path / "settings.json")
    upload = UploadSettings(4, 120, 2_000_000, AudioFormat.FLAC)
    store.save_state(Settings((replace(PROVIDER, upload=upload),)))
    assert store.load_state().providers[0].upload == upload
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    data["providers"][0].pop("upload")
    (tmp_path / "settings.json").write_text(json.dumps(data), encoding="utf-8")
    assert store.load_state().providers[0].upload == UploadSettings()


@pytest.mark.parametrize(
    "upload",
    [
        {"parallelism": True},
        {"parallelism": 0},
        {"parallelism": 100},
        {"chunk_seconds": -1},
        {"chunk_seconds": "5"},
        {"chunk_bytes": 25_000_001},
        {"chunk_bytes": True},
        {"format": "invalid"},
    ],
)
def test_damaged_upload_settings_are_refused(tmp_path: Path, upload: dict[str, object]) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.save_state(Settings((PROVIDER,)))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["providers"][0]["upload"] = upload
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SettingsError):
        store.load_state()


def test_audio_llm_prompt_and_format_load_back(tmp_path: Path) -> None:
    llm = LlmSettings(LlmFormat.GEMINI, "只转写。")
    provider = Provider("p2", "Gemini LLM", ApiKind.AUDIO_LLM, "https://example.test", llm=llm)
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((provider,)))
    assert store.load_state().providers == (provider,)


def test_llm_settings_are_written_only_for_audio_llm(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    SettingsStore(path).save_state(Settings((PROVIDER,)))
    assert "llm" not in json.loads(path.read_text(encoding="utf-8"))["providers"][0]


def test_audio_llm_saved_without_llm_settings_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    entry = {"id": "p", "name": "X", "api": "audio-llm", "base_url": "https://x"}
    path.write_text(json.dumps({"version": 2, "providers": [entry]}), encoding="utf-8")
    assert SettingsStore(path).load_state().providers[0].llm == LlmSettings()
