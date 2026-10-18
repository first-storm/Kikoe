"""Providers, preferences and the window size share one settings file and one writer."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from kikoe.core.models import ApiKind, Capabilities, ModelSpec
from kikoe.core.preferences import WindowState
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.storage.settings import Settings, SettingsError, SettingsStore
from kikoe.storage.settings_writer import SettingsWriter
from kikoe.storage.window_state import WindowStateStore
from tests.helpers import open_settings, run_async

URL = "https://api.deepgram.com/v1"


class MemorySecrets:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self.items.get(provider_id)

    def set(self, provider_id: str, api_key: str) -> None:
        self.items[provider_id] = api_key

    def delete(self, provider_id: str) -> None:
        self.items.pop(provider_id, None)


class Repositories:
    """All three stores over one writer, as the application builds them."""

    def __init__(self, path: Path, secrets: MemorySecrets | None = None) -> None:
        self.writer = open_settings(path)
        self.secrets = secrets or MemorySecrets()
        self.providers = ProviderRepository(self.writer, self.secrets)
        self.preferences = PreferencesRepository(self.writer)
        self.window = WindowStateStore(self.writer)

    async def load(self) -> None:
        await self.providers.load()
        await self.preferences.load()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "settings.json"


@run_async
async def test_concurrent_defaults_and_provider_edits_both_survive(path: Path) -> None:
    stores = Repositories(path)
    provider = await stores.providers.add_provider("Cloud", ApiKind.DEEPGRAM, URL, "")
    await asyncio.gather(
        stores.preferences.update(lambda p: replace(p, language="zh")),
        stores.providers.save_model(provider.provider_id, ModelSpec("a", "A", Capabilities())),
        stores.providers.save_model(provider.provider_id, ModelSpec("b", "B", Capabilities())),
        stores.preferences.update(lambda p: replace(p, recent_model=(provider.provider_id, "b"))),
        stores.window.update(WindowState(1000, 700, maximized=True)),
    )
    restarted = Repositories(path, stores.secrets)
    await restarted.load()
    assert restarted.preferences.current.language == "zh"
    assert restarted.preferences.current.recent_model == (provider.provider_id, "b")
    assert "default_model" not in json.loads(read(path))["preferences"]
    assert [m.model_id for m in restarted.providers.get(provider.provider_id).models] == ["a", "b"]
    assert restarted.window.window == WindowState(1000, 700, maximized=True)


@run_async
async def test_one_file_holds_providers_preferences_and_window_size(path: Path) -> None:
    stores = Repositories(path)
    await stores.providers.add_provider("Cloud", ApiKind.OPENAI, URL, "secret")
    await stores.preferences.update(lambda p: replace(p, language="zh"))
    await stores.window.update(WindowState(900, 600, maximized=False))

    document = json.loads(read(path))

    assert {"version", "providers", "preferences", "window"} <= document.keys()
    assert document["window"] == {"width": 900, "height": 600, "maximized": False}
    assert document["preferences"]["language"] == "zh"
    assert [p["name"] for p in document["providers"]] == ["Cloud"]
    assert "secret" not in read(path)


@run_async
async def test_window_size_survives_provider_and_preference_edits(path: Path) -> None:
    stores = Repositories(path)
    await stores.window.update(WindowState(1000, 700, maximized=True))
    await stores.providers.add_provider("Cloud", ApiKind.OPENAI, URL, "")
    await stores.preferences.update(lambda p: replace(p, language="zh"))

    restarted = Repositories(path, stores.secrets)
    await restarted.load()

    assert restarted.window.window == WindowState(1000, 700, maximized=True)
    assert [p.name for p in restarted.providers.providers] == ["Cloud"]


@run_async
async def test_preference_listeners_hear_each_committed_edit(path: Path) -> None:
    stores = Repositories(path)
    calls: list[str] = []
    stores.preferences.connect_changed(lambda: calls.append("preferences"))
    stores.providers.connect_changed(lambda: calls.append("providers"))

    await stores.preferences.update(lambda p: replace(p, language="zh"))
    await stores.window.update(WindowState(900, 700, maximized=False))

    assert calls == ["preferences"]


@run_async
async def test_load_announces_providers_then_preferences(path: Path) -> None:
    stores = Repositories(path)
    calls: list[str] = []
    stores.providers.connect_changed(lambda: calls.append("providers"))
    stores.preferences.connect_changed(lambda: calls.append("preferences"))

    await stores.load()

    assert calls == ["providers", "preferences"]


@run_async
async def test_failed_preference_save_does_not_notify_or_change_memory(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = Repositories(path)
    calls: list[str] = []
    stores.preferences.connect_changed(lambda: calls.append("changed"))

    def fail_save(self: SettingsStore, state: Settings) -> None:
        raise SettingsError("Cannot save")

    monkeypatch.setattr(SettingsStore, "save_state", fail_save)
    with pytest.raises(SettingsError):
        await stores.preferences.update(lambda p: replace(p, language="zh"))
    assert stores.preferences.current.language is None
    assert calls == []


@run_async
async def test_failed_window_save_keeps_state_and_does_not_notify(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stores = Repositories(path)
    await stores.load()
    original = stores.window.window
    calls: list[str] = []
    stores.providers.connect_changed(lambda: calls.append("providers"))
    stores.preferences.connect_changed(lambda: calls.append("preferences"))

    def fail_save(self: SettingsStore, state: Settings) -> None:
        raise SettingsError("Cannot save")

    monkeypatch.setattr(SettingsStore, "save_state", fail_save)
    with pytest.raises(SettingsError):
        await stores.window.update(WindowState(900, 700, maximized=False))
    assert stores.window.window == original
    assert calls == []


@run_async
async def test_no_edit_can_overwrite_damaged_settings_after_a_failed_startup(path: Path) -> None:
    write(path, "damaged settings")
    stores = Repositories(path)
    with pytest.raises(SettingsError):
        await stores.load()
    with pytest.raises(SettingsError):
        await stores.preferences.update(lambda p: replace(p, language="zh"))
    with pytest.raises(SettingsError):
        await stores.window.update(WindowState(900, 700, maximized=False))
    with pytest.raises(SettingsError):
        await stores.providers.add_provider("Cloud", ApiKind.OPENAI, URL, "secret")
    assert read(path) == "damaged settings"
    assert stores.secrets.items == {}


def test_a_new_writer_starts_with_default_settings_until_it_reads_the_file(path: Path) -> None:
    writer: SettingsWriter = open_settings(path)
    assert writer.state == Settings()
