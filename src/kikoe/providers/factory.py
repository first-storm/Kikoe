"""Pick the client that speaks a provider's API."""

from kikoe.core.models import ApiKind, Provider
from kikoe.providers.base import TranscriptionProvider
from kikoe.providers.deepgram import DeepgramClient
from kikoe.providers.gemini import GeminiClient
from kikoe.providers.mimo import MiMoClient
from kikoe.providers.openai import OpenAIClient
from kikoe.providers.openrouter import OpenRouterClient


def create_client(provider: Provider, api_key: str) -> TranscriptionProvider:
    """Return a client for ``provider``; raise :class:`TranscriptionError` if unsupported."""
    match provider.api:
        case ApiKind.OPENAI | ApiKind.OPENAI_COMPATIBLE:
            return OpenAIClient(provider.base_url, api_key)
        case ApiKind.DEEPGRAM:
            return DeepgramClient(provider.base_url, api_key)
        case ApiKind.OPENROUTER:
            return OpenRouterClient(provider.base_url, api_key)
        case ApiKind.GEMINI:
            return GeminiClient(provider.base_url, api_key)
        case ApiKind.MIMO:
            return MiMoClient(provider.base_url, api_key)
