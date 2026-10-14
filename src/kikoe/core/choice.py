"""Which transcription model is in use, kept valid while providers change."""

from collections.abc import Iterable
from dataclasses import dataclass

from kikoe.core.models import ApiKind, ModeCapabilities, ModelSpec, Provider, TimestampGranularity
from kikoe.core.validation import Capability


@dataclass(frozen=True, slots=True)
class ModelChoice:
    provider: Provider
    model: ModelSpec

    @property
    def key(self) -> tuple[str, str]:
        """Stable identity, since a provider or model may be replaced by an edited copy."""
        return self.provider.provider_id, self.model.model_id


def available_models(providers: Iterable[Provider]) -> tuple[ModelChoice, ...]:
    """Every model of every provider, in settings order."""
    return tuple(ModelChoice(p, m) for p in providers for m in p.models)


def resolve_choice(
    providers: Iterable[Provider], wanted: tuple[str, str] | None
) -> ModelChoice | None:
    """The model with key ``wanted``, refreshed from ``providers``.

    Falls back to the first model when it was removed, so there is always a model to
    transcribe with as long as one exists.
    """
    choices = available_models(providers)
    return next((c for c in choices if c.key == wanted), choices[0] if choices else None)


@dataclass(frozen=True, slots=True)
class RequestedFeatures:
    """What the user asked the next transcription to include (the model popover's options)."""

    timestamps: bool = True
    diarization: bool = True
    timestamp_granularities: tuple[TimestampGranularity, ...] = (TimestampGranularity.SEGMENT,)
    """Timestamp detail; honoured only by models with word timestamps."""

    def allowed_by(self, capabilities: ModeCapabilities) -> RequestedFeatures:
        """Drop what the model cannot do; those options are shown as unavailable."""
        return RequestedFeatures(
            timestamps=self.timestamps and capabilities.timestamps,
            diarization=self.diarization and capabilities.diarization,
            timestamp_granularities=normalize_granularities(self.timestamp_granularities)
            if capabilities.word_timestamps
            else (TimestampGranularity.SEGMENT,),
        )


def normalize_granularities(
    values: Iterable[TimestampGranularity],
) -> tuple[TimestampGranularity, ...]:
    """Deduplicate a granularity selection into canonical order, never empty."""
    selected = set(values)
    ordered = tuple(g for g in TimestampGranularity if g in selected)
    return ordered or (TimestampGranularity.SEGMENT,)


def unsupported_features(capabilities: ModeCapabilities) -> tuple[Capability, ...]:
    """Requestable features the model lacks, in display order, for the bar's hint."""
    pairs = (
        (capabilities.timestamps, Capability.TIMESTAMPS),
        (capabilities.diarization, Capability.DIARIZATION),
    )
    return tuple(capability for supported, capability in pairs if not supported)


def language_dropped(api: ApiKind, features: RequestedFeatures, language: str | None) -> bool:
    """Whether a set transcription language is left out of the request.

    Mistral rejects a language together with timestamp granularities, and speaker labels
    need its segment timestamps, so either option makes the service detect the language.
    ``features`` must already be limited to what the model supports.
    """
    return (
        api is ApiKind.MISTRAL and bool(language) and (features.timestamps or features.diarization)
    )
