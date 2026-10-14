"""Pick the client that speaks a provider's API.

Each client is imported when it is first built: its SDK takes hundreds of milliseconds
to load and most sessions only use one of them.
"""

from collections.abc import Callable

from kikoe.core.models import ApiKind, Provider
from kikoe.providers.base import TranscriptionError, TranscriptionProvider

type ClientFactory = Callable[[str, str], TranscriptionProvider]
"""Builds a client from a base URL and an API key."""


def _deepgram(url: str, key: str) -> TranscriptionProvider:
    from kikoe.providers.deepgram import DeepgramClient

    return DeepgramClient(url, key)


def _openai(url: str, key: str) -> TranscriptionProvider:
    from kikoe.providers.openai import OpenAIClient

    return OpenAIClient(url, key)


def _openrouter(url: str, key: str) -> TranscriptionProvider:
    from kikoe.providers.openrouter import OpenRouterClient

    return OpenRouterClient(url, key)


def _gemini(url: str, key: str) -> TranscriptionProvider:
    from kikoe.providers.gemini import GeminiClient

    return GeminiClient(url, key)


def _mimo(url: str, key: str) -> TranscriptionProvider:
    from kikoe.providers.mimo import MiMoClient

    return MiMoClient(url, key)


def _groq(url: str, key: str) -> TranscriptionProvider:
    from kikoe.providers.groq import GroqClient

    return GroqClient(url, key)


def _mistral(url: str, key: str) -> TranscriptionProvider:
    from kikoe.providers.mistral import MistralClient

    return MistralClient(url, key)


CLIENTS: dict[ApiKind, ClientFactory] = {
    ApiKind.DEEPGRAM: _deepgram,
    ApiKind.OPENAI: _openai,
    ApiKind.OPENAI_COMPATIBLE: _openai,
    ApiKind.OPENROUTER: _openrouter,
    ApiKind.GEMINI: _gemini,
    ApiKind.MIMO: _mimo,
    ApiKind.GROQ: _groq,
    ApiKind.MISTRAL: _mistral,
}
"""One entry per ``ApiKind``; ``tests/test_provider_factory.py`` fails when one is missing."""


def create_client(provider: Provider, api_key: str) -> TranscriptionProvider:
    """Return a client for ``provider``; raise :class:`TranscriptionError` if unsupported."""
    factory = CLIENTS.get(provider.api)
    if factory is None:
        raise TranscriptionError(f"No client is available for {provider.api.value} providers")
    return factory(provider.base_url, api_key)
