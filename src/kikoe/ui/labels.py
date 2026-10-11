"""Translated texts for core enums and validation codes."""

from kikoe.core.models import ApiKind, Capabilities
from kikoe.core.validation import Capability, ModelError, ProviderError, capability_list
from kikoe.i18n import _


def api_label(api: ApiKind) -> str:
    match api:
        case ApiKind.DEEPGRAM:
            return _("Deepgram")
        case ApiKind.OPENAI:
            return _("OpenAI")
        case ApiKind.OPENAI_COMPATIBLE:
            return _("OpenAI-compatible")


def capability_label(capability: Capability) -> str:
    match capability:
        case Capability.REALTIME:
            return _("Realtime")
        case Capability.TIMESTAMPS:
            return _("Timestamps")
        case Capability.DIARIZATION:
            return _("Speakers")


def capability_summary(capabilities: Capabilities) -> str:
    """E.g. “Realtime · Timestamps”, or “Text only” when the model has none."""
    labels = [capability_label(c) for c in capability_list(capabilities)]
    return " · ".join(labels) if labels else _("Text only")


def provider_error_message(error: ProviderError) -> str:
    match error:
        case ProviderError.EMPTY_NAME:
            return _("Enter a name for this provider.")
        case ProviderError.EMPTY_BASE_URL:
            return _("Enter the base URL of the API.")
        case ProviderError.INVALID_BASE_URL:
            return _("The base URL must start with http:// or https:// and include a host name.")


def model_error_message(error: ModelError) -> str:
    match error:
        case ModelError.EMPTY_MODEL_ID:
            return _("Enter the model ID.")
        case ModelError.DUPLICATE_MODEL_ID:
            return _("This provider already has a model with that ID.")
