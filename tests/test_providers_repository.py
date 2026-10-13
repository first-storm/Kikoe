from pathlib import Path

import pytest

from helpers import file_caps, run_async
from kikoe.core.models import ApiKind, Capabilities, ModeCapabilities, ModelSpec, Provider
from kikoe.storage.providers import ProviderNotFoundError, ProviderRepository
from kikoe.storage.settings import SettingsStore

URL = "https://api.groq.com/openai/v1"


class FakeSecrets:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self.items.get(provider_id)

    def set(self, provider_id: str, api_key: str) -> None:
        self.items[provider_id] = api_key

    def delete(self, provider_id: str) -> None:
        self.items.pop(provider_id, None)


@pytest.fixture
def secrets() -> FakeSecrets:
    return FakeSecrets()


@pytest.fixture
def repo(tmp_path: Path, secrets: FakeSecrets) -> ProviderRepository:
    return ProviderRepository(SettingsStore(tmp_path / "settings.json"), secrets)


@run_async
async def test_added_provider_survives_a_restart(
    tmp_path: Path, repo: ProviderRepository, secrets: FakeSecrets
) -> None:
    provider = await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "k1")

    restarted = ProviderRepository(SettingsStore(tmp_path / "settings.json"), secrets)
    await restarted.load()

    assert restarted.providers == (provider,)
    assert await restarted.get_api_key(provider.provider_id) == "k1"


@run_async
async def test_api_key_is_kept_out_of_the_settings_file(
    tmp_path: Path, repo: ProviderRepository
) -> None:
    await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "super-secret")
    assert "super-secret" not in (tmp_path / "settings.json").read_text(encoding="utf-8")


@run_async
async def test_update_changes_details_and_clears_an_emptied_key(
    repo: ProviderRepository, secrets: FakeSecrets
) -> None:
    provider = await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "k1")

    await repo.update_provider(provider.provider_id, " Groq 2 ", ApiKind.OPENAI, URL, "")

    updated = repo.get(provider.provider_id)
    assert (updated.name, updated.api) == ("Groq 2", ApiKind.OPENAI)
    assert secrets.items == {}


@run_async
async def test_removing_a_provider_deletes_its_key(
    repo: ProviderRepository, secrets: FakeSecrets
) -> None:
    provider = await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "k1")

    await repo.remove_provider(provider.provider_id)

    assert repo.providers == ()
    assert secrets.items == {}


@run_async
async def test_model_can_be_added_edited_and_removed(repo: ProviderRepository) -> None:
    provider = await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "")
    first = ModelSpec("a", "A", Capabilities())
    second = ModelSpec("b", "B", file_caps(timestamps=True))
    await repo.save_model(provider.provider_id, first)
    await repo.save_model(provider.provider_id, second)

    renamed = ModelSpec("a2", "A2", Capabilities(realtime=ModeCapabilities()))
    await repo.save_model(provider.provider_id, renamed, replacing_id="a")
    assert repo.get(provider.provider_id).models == (renamed, second)

    await repo.remove_model(provider.provider_id, "a2")
    assert repo.get(provider.provider_id).models == (second,)


@run_async
async def test_editing_an_unknown_model_is_an_error(repo: ProviderRepository) -> None:
    provider = await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "")
    with pytest.raises(ProviderNotFoundError):
        await repo.save_model(provider.provider_id, ModelSpec("x", "X", Capabilities()), "gone")


@run_async
async def test_listeners_are_told_about_changes(repo: ProviderRepository) -> None:
    calls: list[int] = []
    repo.connect_changed(lambda: calls.append(len(repo.providers)))

    await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "")

    assert calls == [1]


@run_async
async def test_concurrent_defaults_and_provider_edits_both_survive(
    repo: ProviderRepository, tmp_path: Path, secrets: FakeSecrets
) -> None:
    import asyncio
    from dataclasses import replace

    provider = await repo.add_provider("Cloud", ApiKind.DEEPGRAM, URL, "")
    await asyncio.gather(
        repo.update_preferences(lambda p: replace(p, language="zh")),
        repo.save_model(provider.provider_id, ModelSpec("a", "A", Capabilities())),
        repo.save_model(provider.provider_id, ModelSpec("b", "B", Capabilities())),
        repo.update_preferences(lambda p: replace(p, default_model=(provider.provider_id, "b"))),
    )
    restarted = ProviderRepository(SettingsStore(tmp_path / "settings.json"), secrets)
    await restarted.load()
    assert restarted.preferences.language == "zh"
    assert restarted.preferences.default_model == (provider.provider_id, "b")
    assert [m.model_id for m in restarted.get(provider.provider_id).models] == ["a", "b"]


@run_async
async def test_failed_defaults_save_does_not_notify_or_change_memory(
    repo: ProviderRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from kikoe.storage.settings import Settings, SettingsError

    calls: list[str] = []
    repo.connect_preferences_changed(lambda: calls.append("changed"))

    def fail_save(self: SettingsStore, state: Settings) -> None:
        raise SettingsError("Cannot save")

    monkeypatch.setattr(SettingsStore, "save_state", fail_save)
    with pytest.raises(SettingsError):
        await repo.update_preferences(lambda p: replace(p, language="zh"))
    assert repo.preferences.language is None
    assert calls == []


@run_async
async def test_edit_cannot_overwrite_damaged_settings_after_failed_startup(
    tmp_path: Path, secrets: FakeSecrets
) -> None:
    from dataclasses import replace

    from kikoe.storage.settings import SettingsError

    path = tmp_path / "settings.json"
    path.write_text("damaged settings")
    repository = ProviderRepository(SettingsStore(path), secrets)
    with pytest.raises(SettingsError):
        await repository.load()
    with pytest.raises(SettingsError):
        await repository.update_preferences(lambda p: replace(p, language="zh"))
    with pytest.raises(SettingsError):
        await repository.add_provider("Cloud", ApiKind.OPENAI, URL, "secret")
    assert path.read_text() == "damaged settings"
    assert secrets.items == {}


@run_async
async def test_window_state_persists_and_keeps_providers_and_defaults(
    tmp_path: Path, repo: ProviderRepository, secrets: FakeSecrets
) -> None:
    from dataclasses import replace

    from kikoe.core.preferences import WindowState

    await repo.add_provider("Cloud", ApiKind.OPENAI, URL, "secret")
    await repo.update_preferences(lambda p: replace(p, language="zh"))

    await repo.update_window(WindowState(1000, 700, maximized=True))

    reloaded = ProviderRepository(SettingsStore(tmp_path / "settings.json"), secrets)
    await reloaded.load()
    assert reloaded.window == WindowState(1000, 700, maximized=True)
    assert [p.name for p in reloaded.providers] == ["Cloud"]
    assert reloaded.preferences.language == "zh"


ALL_ON = Capabilities(
    file=ModeCapabilities(True, True, True, True), realtime=ModeCapabilities(True, True, True, True)
)


@run_async
async def test_saved_models_lose_options_the_endpoint_rejects(repo: ProviderRepository) -> None:
    provider = await repo.add_provider("OpenAI", ApiKind.OPENAI, URL, "")
    await repo.save_model(provider.provider_id, ModelSpec("whisper-1", "Whisper", ALL_ON))

    assert repo.get(provider.provider_id).models[0].capabilities == file_caps(timestamps=True)


@run_async
async def test_changing_the_api_type_applies_the_new_limits(repo: ProviderRepository) -> None:
    provider = await repo.add_provider("Deepgram", ApiKind.DEEPGRAM, URL, "")
    await repo.save_model(provider.provider_id, ModelSpec("nova-3", "Nova", ALL_ON))

    await repo.update_provider(provider.provider_id, "Router", ApiKind.OPENROUTER, URL, "")

    capabilities = repo.get(provider.provider_id).models[0].capabilities
    assert capabilities == file_caps(timestamps=True, diarization=True)


@run_async
async def test_loading_clamps_models_saved_by_an_older_catalog(
    tmp_path: Path, secrets: FakeSecrets
) -> None:
    path = tmp_path / "settings.json"
    model = ModelSpec("gpt-live-transcribe", "Live", file_caps(timestamps=True))
    provider = Provider("p", "OpenAI", ApiKind.OPENAI, URL, (model,))
    SettingsStore(path).save((provider,))
    repository = ProviderRepository(SettingsStore(path), secrets)

    await repository.load()

    capabilities = repository.get("p").models[0].capabilities
    assert capabilities == Capabilities(file=None, realtime=ModeCapabilities())
