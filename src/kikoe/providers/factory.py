"""Pick the client that speaks a provider's API."""

from collections.abc import Callable

from kikoe.core.models import ApiKind, Provider
from kikoe.providers.base import TranscriptionError, TranscriptionProvider
from kikoe.providers.deepgram import DeepgramClient
from kikoe.providers.gemini import GeminiClient
from kikoe.providers.groq import GroqClient
from kikoe.providers.mimo import MiMoClient
from kikoe.providers.mistral import MistralClient
from kikoe.providers.openai import OpenAIClient
from kikoe.providers.openrouter import OpenRouterClient

type ClientFactory = Callable[[str, str], TranscriptionProvider]
"""Builds a client from a base URL and an API key."""

CLIENTS: dict[ApiKind, ClientFactory] = {
    ApiKind.DEEPGRAM: DeepgramClient,
    ApiKind.OPENAI: OpenAIClient,
    ApiKind.OPENAI_COMPATIBLE: OpenAIClient,
    ApiKind.OPENROUTER: OpenRouterClient,
    ApiKind.GEMINI: GeminiClient,
    ApiKind.MIMO: MiMoClient,
    ApiKind.GROQ: GroqClient,
    ApiKind.MISTRAL: MistralClient,
}
"""One entry per ``ApiKind``; ``tests/test_provider_factory.py`` fails when one is missing."""


def create_client(provider: Provider, api_key: str) -> TranscriptionProvider:
    """Return a client for ``provider``; raise :class:`TranscriptionError` if unsupported."""
    factory = CLIENTS.get(provider.api)
    if factory is None:
        raise TranscriptionError(f"No client is available for {provider.api.value} providers")
    return factory(provider.base_url, api_key)
