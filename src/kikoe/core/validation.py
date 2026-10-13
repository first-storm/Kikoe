"""Validation of user-entered provider and model details.

Functions return an error code instead of raising so the UI can show the matching
translated message next to the field that caused it.
"""

from collections.abc import Collection
from datetime import timedelta
from enum import StrEnum
from urllib.parse import urlsplit

from kikoe.core.models import ApiKind, Capabilities


class ProviderError(StrEnum):
    EMPTY_NAME = "empty-name"
    EMPTY_BASE_URL = "empty-base-url"
    INVALID_BASE_URL = "invalid-base-url"


class ModelError(StrEnum):
    EMPTY_MODEL_ID = "empty-model-id"
    DUPLICATE_MODEL_ID = "duplicate-model-id"
    NO_MODE = "no-mode"


class Capability(StrEnum):
    """One thing a model can do; the UI maps each to a translated label."""

    REALTIME = "realtime"
    TIMESTAMPS = "timestamps"
    WORD_TIMESTAMPS = "word-timestamps"
    DIARIZATION = "diarization"
    STREAMING = "streaming"


DEFAULT_BASE_URLS: dict[ApiKind, str] = {
    ApiKind.DEEPGRAM: "https://api.deepgram.com/v1",
    ApiKind.OPENAI: "https://api.openai.com/v1",
    ApiKind.OPENROUTER: "https://openrouter.ai/api/v1",
    ApiKind.GEMINI: "https://generativelanguage.googleapis.com",
}
"""Hosted APIs with one well-known endpoint; OpenAI-compatible services have none."""


def validate_provider(name: str, api: ApiKind, base_url: str) -> ProviderError | None:
    """Check the connection fields; an empty URL is fine when the API has a default."""
    if not name.strip():
        return ProviderError.EMPTY_NAME
    if not base_url.strip():
        return None if api in DEFAULT_BASE_URLS else ProviderError.EMPTY_BASE_URL
    parts = urlsplit(base_url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ProviderError.INVALID_BASE_URL
    return None


def effective_base_url(api: ApiKind, base_url: str) -> str:
    """The URL to store: trimmed input, or the API's default when left empty."""
    stripped = base_url.strip().rstrip("/")
    return stripped or DEFAULT_BASE_URLS.get(api, "")


def validate_model(
    model_id: str,
    existing_ids: Collection[str],
    editing_id: str | None = None,
    capabilities: Capabilities | None = None,
) -> ModelError | None:
    """Check a model ID is present and unique within its provider, and can transcribe.

    ``editing_id`` is the model's current ID when editing, so keeping it is not a duplicate.
    """
    stripped = model_id.strip()
    if not stripped:
        return ModelError.EMPTY_MODEL_ID
    if stripped != editing_id and stripped in existing_ids:
        return ModelError.DUPLICATE_MODEL_ID
    if capabilities is not None and capabilities.file is None and capabilities.realtime is None:
        return ModelError.NO_MODE
    return None


def capability_list(capabilities: Capabilities) -> tuple[Capability, ...]:
    """What a model can do in either mode, in display order; empty means text only."""
    modes = tuple(m for m in (capabilities.file, capabilities.realtime) if m is not None)
    file = capabilities.file
    pairs = (
        (capabilities.realtime is not None, Capability.REALTIME),
        (any(m.timestamps for m in modes), Capability.TIMESTAMPS),
        (file is not None and file.word_timestamps, Capability.WORD_TIMESTAMPS),
        (any(m.diarization for m in modes), Capability.DIARIZATION),
        (file is not None and file.streaming, Capability.STREAMING),
    )
    return tuple(capability for enabled, capability in pairs if enabled)


class SegmentEditError(StrEnum):
    """Why an edited transcript segment cannot be saved; the UI shows a message per code."""

    EMPTY_TEXT = "empty-text"
    END_BEFORE_START = "end-before-start"


def validate_segment_edit(
    text: str, start: timedelta | None, end: timedelta | None
) -> SegmentEditError | None:
    """Check edited text and times; either timestamp may be cleared (None)."""
    if not text.strip():
        return SegmentEditError.EMPTY_TEXT
    if start is not None and end is not None and end <= start:
        return SegmentEditError.END_BEFORE_START
    return None
