import asyncio
import functools
from collections.abc import Callable, Coroutine
from pathlib import Path

import pytest

from kikoe.core.models import ApiKind, Capabilities, ModelSpec
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


def run_async[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    """Run an async test body to completion; avoids a pytest plugin for a handful of tests."""

    @functools.wraps(test)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return wrapper


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
    second = ModelSpec("b", "B", Capabilities(timestamps=True))
    await repo.save_model(provider.provider_id, first)
    await repo.save_model(provider.provider_id, second)

    renamed = ModelSpec("a2", "A2", Capabilities(realtime=True))
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
