import pytest

from kikoe.core.models import ApiKind, Provider
from kikoe.providers import factory
from kikoe.providers.base import TranscriptionError
from kikoe.providers.deepgram import DeepgramClient
from kikoe.providers.openai import OpenAIClient


def provider(api: ApiKind) -> Provider:
    return Provider("id", "Test", api, "https://example.test/v1")


def test_every_api_kind_has_a_client() -> None:
    assert set(factory.CLIENTS) == set(ApiKind)


@pytest.mark.parametrize("api", list(ApiKind))
def test_create_client_builds_the_registered_client(api: ApiKind) -> None:
    expected = factory.CLIENTS[api]("https://example.test/v1", "key")
    assert type(factory.create_client(provider(api), "key")) is type(expected)


def test_openai_compatible_services_use_the_openai_client() -> None:
    assert isinstance(factory.create_client(provider(ApiKind.OPENAI_COMPATIBLE), ""), OpenAIClient)
    assert isinstance(factory.create_client(provider(ApiKind.DEEPGRAM), ""), DeepgramClient)


def test_unregistered_api_is_reported_as_a_transcription_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(factory.CLIENTS, ApiKind.GROQ)
    with pytest.raises(TranscriptionError):
        factory.create_client(provider(ApiKind.GROQ), "key")
