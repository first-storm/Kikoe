"""Resolve API limits and accumulate live text without depending on SDKs or GTK."""

from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Segment


@dataclass(frozen=True, slots=True)
class ModelSupport:
    """File and microphone requests can have different effective capabilities."""

    file: bool
    recording: Capabilities
    batch: Capabilities


def model_support(api: ApiKind, model: ModelSpec) -> ModelSupport:
    """Manual declarations cannot enable options rejected by a known endpoint."""
    caps = model.capabilities
    batch = replace(caps, realtime=False)
    file = True
    match api:
        case ApiKind.OPENROUTER:
            caps = replace(caps, realtime=False)
        case ApiKind.GEMINI:
            live = model.model_id == "gemini-3.5-transcribe-live"
            file = not live
            caps = Capabilities(realtime=True) if live else replace(caps, realtime=False)
        case ApiKind.OPENAI | ApiKind.OPENAI_COMPATIBLE:
            file = model.model_id not in {"gpt-live-transcribe", "gpt-realtime-whisper"}
            if model.model_id == "whisper-1":
                caps = replace(caps, realtime=False, diarization=False)
                batch = replace(batch, diarization=False)
            elif model.model_id == "gpt-4o-transcribe-diarize":
                caps = replace(caps, realtime=False)
            else:
                if model.model_id in {"gpt-live-transcribe", "gpt-realtime-whisper"}:
                    caps = replace(caps, realtime=True)
                if model.model_id.startswith(("gpt-4o", "gpt-transcribe")):
                    batch = replace(batch, timestamps=False, diarization=False)
                if caps.realtime:
                    caps = replace(caps, timestamps=False, diarization=False)
        case ApiKind.DEEPGRAM:
            pass
    return ModelSupport(file, caps, batch)


def scope_speakers(segments: tuple[Segment, ...], scope: str) -> tuple[Segment, ...]:
    """Independent recognition sessions must not claim that same-numbered people match."""
    return tuple(
        replace(s, speaker_id=f"{scope}:{s.speaker_id}" if s.speaker_id is not None else None)
        for s in segments
    )


@dataclass(frozen=True, slots=True)
class TranscriptEvent:
    """A replacement for one utterance; only final events survive recording recovery."""

    id: str
    segments: tuple[Segment, ...]
    final: bool
    audio_end: timedelta | None = None


class LiveState(StrEnum):
    """Connection status is separate from the microphone's capture state."""

    CONNECTING = "connecting"
    LIVE = "live"
    RECONNECTING = "reconnecting"
    CATCHING_UP = "catching-up"
    INCOMPLETE = "incomplete"
    PAUSED = "paused"


@dataclass(frozen=True, slots=True)
class LiveSnapshot:
    """Immutable presentation data shared by recording services and the UI."""

    segments: tuple[Segment, ...]
    preview: tuple[Segment, ...]
    state: LiveState


class LiveTranscript:
    """Replace previews and repeated finals, maintaining provider-defined utterance order."""

    def __init__(self) -> None:
        self._final: dict[str, tuple[Segment, ...]] = {}
        self._preview: tuple[Segment, ...] = ()

    @property
    def segments(self) -> tuple[Segment, ...]:
        return tuple(s for group in self._final.values() for s in group)

    @property
    def preview(self) -> tuple[Segment, ...]:
        return self._preview

    def apply(self, event: TranscriptEvent) -> None:
        if event.final:
            self._final[event.id] = event.segments
            self._preview = ()
        elif event.id not in self._final:
            self._preview = event.segments


class SpeechBoundary:
    """Bound manual-commit turns even when someone speaks without pausing."""

    def __init__(self) -> None:
        self._duration = timedelta()
        self._silence = timedelta()
        self._speech = False

    def feed(self, duration: timedelta, level: float) -> bool:
        if self._speech or level > 0.25:
            self._duration += duration
        if level > 0.25:
            self._speech = True
            self._silence = timedelta()
        else:
            self._silence += duration
        ready = self._speech and (
            self._silence >= timedelta(milliseconds=500) or self._duration >= timedelta(seconds=15)
        )
        if ready:
            self._duration = self._silence = timedelta()
            self._speech = False
        return ready
