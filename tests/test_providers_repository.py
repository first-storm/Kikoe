from pathlib import Path

import pytest

from helpers import file_caps, open_settings, run_async
from kikoe.core.models import ApiKind, Capabilities, ModeCapabilities, ModelSpec, Provider
from kikoe.storage.providers import ProviderNotFoundError, ProviderRepository
from kikoe.storage.settings import Settings, SettingsStore

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
    return ProviderRepository(open_settings(tmp_path / "settings.json"), secrets)


@run_async
async def test_added_provider_survives_a_restart(
    tmp_path: Path, repo: ProviderRepository, secrets: FakeSecrets
) -> None:
    provider = await repo.add_provider("Groq", ApiKind.OPENAI_COMPATIBLE, URL, "k1")

    restarted = ProviderRepository(open_settings(tmp_path / "settings.json"), secrets)
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
    SettingsStore(path).save_state(Settings((provider,)))
    repository = ProviderRepository(open_settings(path), secrets)

    await repository.load()

    capabilities = repository.get("p").models[0].capabilities
    assert capabilities == Capabilities(file=None, realtime=ModeCapabilities())


@run_async
async def test_upload_edits_preserve_models_credentials_and_other_providers(
    repo: ProviderRepository,
    secrets: FakeSecrets,
) -> None:
    from kikoe.core.models import AudioFormat, UploadSettings

    first = await repo.add_provider("First", ApiKind.OPENAI_COMPATIBLE, URL, "secret")
    second = await repo.add_provider("Second", ApiKind.OPENAI, URL, "other")
    model = ModelSpec("custom", "Custom", file_caps())
    await repo.save_model(first.provider_id, model)
    upload = UploadSettings(3, 60, 1_000_000, AudioFormat.FLAC)
    await repo.update_upload(first.provider_id, upload)
    assert repo.get(first.provider_id).upload == upload
    assert repo.get(first.provider_id).models == (model,)
    assert repo.get(second.provider_id) == second
    assert secrets.items == {first.provider_id: "secret", second.provider_id: "other"}
    await repo.update_provider(first.provider_id, "Renamed", first.api, URL, "secret")
    assert repo.get(first.provider_id).upload == upload
    await repo.update_provider(first.provider_id, "Changed", ApiKind.MIMO, URL, "secret")
    assert repo.get(first.provider_id).upload == UploadSettings()


@run_async
async def test_provider_edit_keeps_upload_and_models_changed_while_saving_the_key(
    repo: ProviderRepository, secrets: FakeSecrets, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import threading

    from kikoe.core.models import UploadSettings

    provider = await repo.add_provider("Cloud", ApiKind.OPENAI_COMPATIBLE, URL, "")
    started = threading.Event()
    release = threading.Event()

    def save_key(provider_id: str, api_key: str) -> None:
        started.set()
        if not release.wait(5):
            raise TimeoutError("Provider edit did not release the key write")
        secrets.items[provider_id] = api_key

    monkeypatch.setattr(secrets, "set", save_key)
    edit = asyncio.create_task(
        repo.update_provider(provider.provider_id, " Renamed ", provider.api, URL, "new-key")
    )
    upload = UploadSettings(parallelism=3, chunk_seconds=60)
    model = ModelSpec("custom", "Custom", file_caps())
    try:
        assert await asyncio.to_thread(started.wait, 5)
        await repo.update_upload(provider.provider_id, upload)
        await repo.save_model(provider.provider_id, model)
    finally:
        release.set()
        await edit

    updated = repo.get(provider.provider_id)
    assert updated.name == "Renamed"
    assert updated.upload == upload
    assert updated.models == (model,)
    assert secrets.items[provider.provider_id] == "new-key"
