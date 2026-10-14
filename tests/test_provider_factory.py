"""The factory hands each API kind its own client."""

import pytest

from kikoe.core.models import ApiKind, Provider
from kikoe.providers import factory
from kikoe.providers.base import TranscriptionError
from kikoe.providers.deepgram import DeepgramClient
from kikoe.providers.gemini import GeminiClient
from kikoe.providers.groq import GroqClient
from kikoe.providers.mimo import MiMoClient
from kikoe.providers.mistral import MistralClient
from kikoe.providers.openai import OpenAIClient
from kikoe.providers.openrouter import OpenRouterClient

EXPECTED_CLIENTS: dict[ApiKind, type[object]] = {
    ApiKind.DEEPGRAM: DeepgramClient,
    ApiKind.OPENAI: OpenAIClient,
    ApiKind.OPENAI_COMPATIBLE: OpenAIClient,
    ApiKind.OPENROUTER: OpenRouterClient,
    ApiKind.GEMINI: GeminiClient,
    ApiKind.MIMO: MiMoClient,
    ApiKind.GROQ: GroqClient,
    ApiKind.MISTRAL: MistralClient,
}
"""Written out by hand so the test does not just repeat ``factory.CLIENTS``."""


def provider(api: ApiKind) -> Provider:
    return Provider("id", "Test", api, "https://example.test/v1")


def test_every_api_kind_has_a_client() -> None:
    assert set(factory.CLIENTS) == set(EXPECTED_CLIENTS) == set(ApiKind)


@pytest.mark.parametrize(("api", "expected"), EXPECTED_CLIENTS.items())
def test_create_client_builds_the_official_client_for_each_api(
    api: ApiKind, expected: type[object]
) -> None:
    assert type(factory.create_client(provider(api), "key")) is expected


def test_unregistered_api_is_reported_as_a_transcription_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(factory.CLIENTS, ApiKind.GROQ)
    with pytest.raises(TranscriptionError):
        factory.create_client(provider(ApiKind.GROQ), "key")
