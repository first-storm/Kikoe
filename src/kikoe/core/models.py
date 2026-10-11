"""Domain types. Immutable; produce modified copies with ``dataclasses.replace``.

See docs/design.md for how these appear in the UI.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path


class ApiKind(StrEnum):
    """Request format a provider speaks. Decides which client in kikoe.providers is used."""

    DEEPGRAM = "deepgram"
    OPENAI = "openai"
    OPENAI_COMPATIBLE = "openai-compatible"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a model can do. Ticked by the user when adding the model."""

    realtime: bool = False
    """Streams text while recording. Otherwise audio is uploaded after recording stops."""
    timestamps: bool = False
    """Segments carry times. Required for subtitle export."""
    diarization: bool = False
    """Segments are attributed to distinct speakers."""


@dataclass(frozen=True, slots=True)
class ModelSpec:
    model_id: str
    """Identifier sent to the API, e.g. ``nova-3`` or ``whisper-1``."""
    display_name: str
    capabilities: Capabilities


@dataclass(frozen=True, slots=True)
class Provider:
    """A configured transcription service. The API key lives in the OS keyring, not here."""

    provider_id: str
    name: str
    api: ApiKind
    base_url: str
    models: tuple[ModelSpec, ...] = ()


@dataclass(frozen=True, slots=True)
class Speaker:
    speaker_id: str
    name: str


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    start: timedelta | None = None
    end: timedelta | None = None
    speaker_id: str | None = None


@dataclass(frozen=True, slots=True)
class Recording:
    """One recorded or imported piece of audio inside a note, with its transcript."""

    recording_id: str
    created_at: datetime
    duration: timedelta
    model_id: str
    segments: tuple[Segment, ...] = ()
    audio_path: Path | None = None
    """Retained original audio, used for re-transcription and raw export."""


@dataclass(frozen=True, slots=True)
class Note:
    """A note holds one or more recordings; later recordings are appended."""

    note_id: str
    title: str
    created_at: datetime
    recordings: tuple[Recording, ...] = ()
    speakers: tuple[Speaker, ...] = ()
