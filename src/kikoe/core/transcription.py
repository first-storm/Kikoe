"""Accumulate live and file transcript text without depending on SDKs or GTK."""

from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from kikoe.core.models import Segment


class CheckpointStrategy(StrEnum):
    """How a realtime service tells the client that audio has been fully recognised."""

    FLUSH = "flush"
    """Ask the service to finish pending audio; replay checkpoints follow each flush."""
    AUDIO_END = "audio-end"
    """The service reports the audio range each final result covers."""


@dataclass(frozen=True, slots=True)
class RealtimeProfile:
    """How a provider wants microphone audio delivered and when its sessions end."""

    sample_rate: int = 16000
    """Mono 16-bit PCM rate the service expects."""
    checkpoint: CheckpointStrategy = CheckpointStrategy.FLUSH
    max_session: timedelta | None = None
    """Longest a connection may live before it is replaced; None means unlimited."""


def scope_speakers(segments: tuple[Segment, ...], scope: str) -> tuple[Segment, ...]:
    """Independent recognition sessions must not claim that same-numbered people match."""
    return tuple(
        replace(s, speaker_id=f"{scope}:{s.speaker_id}" if s.speaker_id is not None else None)
        for s in segments
    )


@dataclass(frozen=True, slots=True)
class FileTranscriptSnapshot:
    """File previews are presentation data; only a successful request can be saved."""

    segments: tuple[Segment, ...] = ()
    preview: tuple[Segment, ...] = ()


class SnapshotScope:
    """Scope a growing stream of snapshots, redoing only the segments that changed.

    Each event repeats every completed segment, so scoping them all would cost more
    with every sentence; this keeps the scoped copies of the unchanged start.
    """

    def __init__(self, scope: str) -> None:
        self._scope = scope
        self._source: tuple[Segment, ...] = ()
        self._scoped: tuple[Segment, ...] = ()

    def apply(self, snapshot: FileTranscriptSnapshot) -> FileTranscriptSnapshot:
        """Keep preview speaker labels consistent with the committed recording."""
        kept = 0
        for old, new in zip(self._source, snapshot.segments, strict=False):
            if old != new:
                break
            kept += 1
        rest = scope_speakers(snapshot.segments[kept:], self._scope)
        self._scoped = self._scoped[:kept] + rest
        self._source = snapshot.segments
        return FileTranscriptSnapshot(self._scoped, scope_speakers(snapshot.preview, self._scope))


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
