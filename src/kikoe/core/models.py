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
    OPENROUTER = "openrouter"
    GEMINI = "gemini"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a model can do. Ticked by the user when adding the model."""

    realtime: bool = False
    """Streams text while recording. Otherwise audio is uploaded after recording stops."""
    timestamps: bool = False
    """Segments carry times. Required for subtitle export."""
    diarization: bool = False
    """Segments are attributed to distinct speakers."""


class TimestampGranularity(StrEnum):
    """Detail level requested in a verbose transcription response."""

    SEGMENT = "segment"
    WORD = "word"


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
    """A note-wide person with a stable display number and theme color slot."""

    speaker_id: str
    name: str
    ordinal: int = 0
    """Stable one-based number; zero is a legacy in-memory speaker."""


@dataclass(frozen=True, slots=True)
class SpeakerBinding:
    """A recognition label and its reversible, note-wide identity assignment."""

    source_id: str
    speaker_id: str
    original_id: str
    confirmed: bool = False


@dataclass(frozen=True, slots=True)
class Segment:
    text: str
    start: timedelta | None = None
    end: timedelta | None = None
    speaker_id: str | None = None


class RecordingSource(StrEnum):
    """Where a recording's audio came from."""

    IMPORTED = "imported"
    RECORDED = "recorded"


class TranscriptionState(StrEnum):
    """Whether retained audio still needs a successful transcription."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Recording:
    """One recorded or imported piece of audio inside a note, with its transcript."""

    recording_id: str
    created_at: datetime
    duration: timedelta
    source: RecordingSource
    provider_name: str
    """The provider's name when transcribed, so the label survives removing the provider."""
    model_id: str
    segments: tuple[Segment, ...] = ()
    audio_path: Path | None = None
    """Retained original audio, used for re-transcription and raw export."""
    transcription_state: TranscriptionState = TranscriptionState.READY
    speaker_bindings: tuple[SpeakerBinding, ...] = ()


@dataclass(frozen=True, slots=True)
class DisplayOptions:
    """How a note is shown and exported. Never changes the stored transcript."""

    show_timestamps: bool = True
    show_speakers: bool = True


@dataclass(frozen=True, slots=True)
class Note:
    """A note holds one or more recordings; later recordings are appended."""

    note_id: str
    title: str
    created_at: datetime
    recordings: tuple[Recording, ...] = ()
    speakers: tuple[Speaker, ...] = ()
    display: DisplayOptions = DisplayOptions()

    @property
    def duration(self) -> timedelta:
        return sum((r.duration for r in self.recordings), timedelta())

    def speaker_name(self, speaker_id: str) -> str | None:
        return next((s.name for s in self.speakers if s.speaker_id == speaker_id), None)


@dataclass(frozen=True, slots=True)
class NoteSummary:
    """What the sidebar shows for a note, without loading its transcript."""

    note_id: str
    title: str
    created_at: datetime
    recording_count: int
    duration: timedelta
    source: RecordingSource | None
    """Source of the first recording; None for a note without recordings."""
