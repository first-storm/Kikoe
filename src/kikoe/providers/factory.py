"""Pick the client that speaks a provider's API.

Each client is imported when it is first built: its SDK takes hundreds of milliseconds
to load and most sessions only use one of them.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kikoe.core.models import ApiKind, Provider
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError, TranscriptionProvider

if TYPE_CHECKING:
    from kikoe.providers.local.client import LocalRuntime


@dataclass(frozen=True, slots=True)
class ClientContext:
    """What clients need beyond a URL and a key; built once by the application."""

    local: LocalRuntime | None = None
    """Installed engines and models; None where local models are not set up."""


type ClientFactory = Callable[[str, str, ClientContext], TranscriptionProvider]
"""Builds a client from a base URL, an API key and the application's context."""


def _deepgram(url: str, key: str, _context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.deepgram import DeepgramClient

    return DeepgramClient(url, key)


def _openai(url: str, key: str, _context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.openai import OpenAIClient

    return OpenAIClient(url, key)


def _openrouter(url: str, key: str, _context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.openrouter import OpenRouterClient

    return OpenRouterClient(url, key)


def _gemini(url: str, key: str, _context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.gemini import GeminiClient

    return GeminiClient(url, key)


def _mimo(url: str, key: str, _context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.mimo import MiMoClient

    return MiMoClient(url, key)


def _groq(url: str, key: str, _context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.groq import GroqClient

    return GroqClient(url, key)


def _mistral(url: str, key: str, _context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.mistral import MistralClient

    return MistralClient(url, key)


def _local(_url: str, _key: str, context: ClientContext) -> TranscriptionProvider:
    from kikoe.providers.local.client import LocalClient

    if context.local is None:
        raise TranscriptionError(_("Local models are not available."))
    return LocalClient(context.local)


CLIENTS: dict[ApiKind, ClientFactory] = {
    ApiKind.DEEPGRAM: _deepgram,
    ApiKind.OPENAI: _openai,
    ApiKind.OPENAI_COMPATIBLE: _openai,
    ApiKind.OPENROUTER: _openrouter,
    ApiKind.GEMINI: _gemini,
    ApiKind.MIMO: _mimo,
    ApiKind.GROQ: _groq,
    ApiKind.MISTRAL: _mistral,
    ApiKind.LOCAL: _local,
}
"""One entry per ``ApiKind``; ``tests/test_provider_factory.py`` fails when one is missing."""


NO_CONTEXT = ClientContext()
"""For cloud providers only, e.g. when warming up an SDK."""


def create_client(
    provider: Provider, api_key: str, context: ClientContext = NO_CONTEXT
) -> TranscriptionProvider:
    """Return a client for ``provider``; raise :class:`TranscriptionError` if unsupported."""
    factory = CLIENTS.get(provider.api)
    if factory is None:
        raise TranscriptionError(f"No client is available for {provider.api.value} providers")
    return factory(provider.base_url, api_key, context)
