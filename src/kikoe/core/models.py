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
    MIMO = "mimo"
    GROQ = "groq"
    MISTRAL = "mistral"


@dataclass(frozen=True, slots=True)
class ModeCapabilities:
    """What one request mode returns. Word timestamps and streaming apply to files only."""

    timestamps: bool = False
    """Segments carry times. Required for subtitle export."""
    word_timestamps: bool = False
    """Times can be requested per word, not only per segment."""
    diarization: bool = False
    """Segments are attributed to distinct speakers."""
    streaming: bool = False
    """Text previews arrive while an uploaded file is still being transcribed."""


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What a model can do per mode, within the limits of kikoe.core.model_catalog."""

    file: ModeCapabilities | None = ModeCapabilities()
    """Finished audio can be uploaded; None means a live-only model."""
    realtime: ModeCapabilities | None = None
    """Text streams while recording; None means recordings are uploaded after they stop."""

    @property
    def recording(self) -> ModeCapabilities:
        """Microphone recordings stream when they can, otherwise they are uploaded."""
        return self.realtime or self.file or ModeCapabilities()


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
class UploadSettings:
    """Per-provider upload choices; None uses the documented provider defaults."""

    parallelism: int = 2
    chunk_seconds: int | None = None
    chunk_bytes: int | None = None
    format: AudioFormat | None = None


class AudioFormat(StrEnum):
    """Upload encodings available in every packaged ffmpeg build."""

    OGG = "ogg"
    FLAC = "flac"
    WAV = "wav"


@dataclass(frozen=True, slots=True)
class UploadPolicy:
    """Known endpoint caps; no duration cap means file size governs automatic splitting."""

    bytes: int
    seconds: int | None = None
    format: AudioFormat = AudioFormat.OGG
    formats: tuple[AudioFormat, ...] = tuple(AudioFormat)


@dataclass(frozen=True, slots=True)
class Provider:
    """A configured transcription service. The API key lives in the OS keyring, not here."""

    provider_id: str
    name: str
    api: ApiKind
    base_url: str
    models: tuple[ModelSpec, ...] = ()
    upload: UploadSettings = UploadSettings()


type ModelKey = tuple[str, str]
"""A model's stable identity: provider ID and model ID."""


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

    def recording(self, recording_id: str) -> Recording | None:
        return next((r for r in self.recordings if r.recording_id == recording_id), None)

    def speaker(self, speaker_id: str | None) -> Speaker | None:
        return next((s for s in self.speakers if s.speaker_id == speaker_id), None)

    def speaker_name(self, speaker_id: str) -> str | None:
        person = self.speaker(speaker_id)
        return person.name if person else None


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
