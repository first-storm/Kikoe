"""Which transcription model is in use, kept valid while providers change."""

from collections.abc import Iterable
from dataclasses import dataclass

from kikoe.core.models import ModelSpec, Provider


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
