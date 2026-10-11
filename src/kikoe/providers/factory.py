"""Pick the client that speaks a provider's API."""

from kikoe.core.models import ApiKind, Provider
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError, TranscriptionProvider
from kikoe.providers.openai import OpenAIClient


def create_client(provider: Provider, api_key: str) -> TranscriptionProvider:
    """Return a client for ``provider``; raise :class:`TranscriptionError` if unsupported."""
    match provider.api:
        case ApiKind.OPENAI | ApiKind.OPENAI_COMPATIBLE:
            return OpenAIClient(provider.base_url, api_key)
        case ApiKind.DEEPGRAM:
            raise TranscriptionError(
                _(
                    "Deepgram is not supported yet. Choose a model from an OpenAI or "
                    "OpenAI-compatible provider."
                )
            )
