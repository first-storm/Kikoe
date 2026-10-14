"""Everything static that is known about each API, in one place.

Adding a provider starts here: one descriptor, one client in ``kikoe.providers``, and
the model presets. Callers look a provider up by ``ApiKind`` instead of branching on it.
"""

from dataclasses import dataclass
from enum import StrEnum

from kikoe.core.models import ApiKind, AudioFormat, UploadPolicy


class UploadHint(StrEnum):
    """Extra explanation the provider page shows next to an upload limit."""

    DURATION_DEPENDS_ON_OPTIONS = "duration-depends-on-options"
    SERVER_MAY_LIMIT_SIZE = "server-may-limit-size"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    """Static facts about one API; nothing here depends on a user's configuration."""

    api: ApiKind
    display_name: str
    upload: UploadPolicy
    default_base_url: str = ""
    """Empty for services that have no single well-known endpoint."""
    generic: bool = False
    """Not a brand name, so the UI translates ``display_name``."""
    language_excludes_timestamps: bool = False
    """The service cannot take a language together with timestamps or speaker labels."""
    upload_hints: frozenset[UploadHint] = frozenset()
    sdk_loggers: tuple[str, ...] = ()
    """Logger names of the SDK, kept quiet because their debug output includes secrets."""


_DESCRIPTORS = (
    ProviderDescriptor(
        ApiKind.DEEPGRAM,
        "Deepgram",
        UploadPolicy(2_000_000_000),
        "https://api.deepgram.com/v1",
        sdk_loggers=("deepgram",),
    ),
    ProviderDescriptor(
        ApiKind.OPENAI,
        "OpenAI",
        UploadPolicy(25_000_000, format=AudioFormat.WAV),
        "https://api.openai.com/v1",
        sdk_loggers=("openai",),
    ),
    ProviderDescriptor(
        ApiKind.OPENAI_COMPATIBLE,
        "OpenAI-compatible",
        UploadPolicy(25_000_000),
        generic=True,
        upload_hints=frozenset({UploadHint.SERVER_MAY_LIMIT_SIZE}),
        sdk_loggers=("openai",),
    ),
    ProviderDescriptor(
        ApiKind.OPENROUTER,
        "OpenRouter",
        UploadPolicy(25_000_000),
        "https://openrouter.ai/api/v1",
        sdk_loggers=("openrouter",),
    ),
    ProviderDescriptor(
        ApiKind.GEMINI,
        "Gemini AI Studio",
        UploadPolicy(2_000_000_000, 3600),
        "https://generativelanguage.googleapis.com",
        upload_hints=frozenset({UploadHint.DURATION_DEPENDS_ON_OPTIONS}),
        sdk_loggers=("google.genai",),
    ),
    ProviderDescriptor(
        ApiKind.MIMO,
        "MiMo",
        # 10 MB of Base64 holds at most 7.5 MB of raw audio.
        UploadPolicy(7_500_000, format=AudioFormat.WAV, formats=(AudioFormat.WAV,)),
        "https://api.xiaomimimo.com/v1",
    ),
    ProviderDescriptor(
        ApiKind.GROQ,
        "Groq",
        UploadPolicy(25_000_000),
        "https://api.groq.com",
        sdk_loggers=("groq",),
    ),
    ProviderDescriptor(
        ApiKind.MISTRAL,
        "Mistral",
        UploadPolicy(500_000_000, 3600),  # Its limits page says 60 minutes, the overview 3 hours.
        "https://api.mistral.ai",
        language_excludes_timestamps=True,
        sdk_loggers=("mistralai",),
    ),
)

PROVIDERS: dict[ApiKind, ProviderDescriptor] = {d.api: d for d in _DESCRIPTORS}
"""In the order the provider page lists them."""


def descriptor(api: ApiKind) -> ProviderDescriptor:
    """The static facts about ``api``."""
    return PROVIDERS[api]


def is_default_base_url(url: str) -> bool:
    """Whether ``url`` is some API's well-known endpoint, so switching APIs may replace it."""
    return url != "" and any(d.default_base_url == url for d in PROVIDERS.values())
