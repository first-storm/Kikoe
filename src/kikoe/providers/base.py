"""The interface every transcription backend implements."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from kikoe.core.choice import RequestedFeatures
from kikoe.core.models import ModeCapabilities, ModelSpec, Segment, TimestampGranularity
from kikoe.core.transcription import FileTranscriptSnapshot, TranscriptEvent
from kikoe.core.upload import UploadLimits

log = logging.getLogger(__name__)

type FileTranscriptCallback = Callable[[FileTranscriptSnapshot], None]


async def close_stack(stack: AsyncExitStack) -> None:
    """Closing a dead connection must not hold up saving a microphone recording."""
    try:
        async with asyncio.timeout(1):
            await stack.aclose()
    except TimeoutError:
        log.warning("The realtime SDK did not finish closing within one second")


@dataclass(frozen=True, slots=True)
class TranscriptionOptions:
    """Per-request switches. Only honoured when the model's Capabilities allow them."""

    timestamps: bool = True
    diarization: bool = True
    language: str | None = None
    """BCP 47 tag, or None to let the service detect it."""
    timestamp_granularities: tuple[TimestampGranularity, ...] = field(
        default=(TimestampGranularity.SEGMENT,), kw_only=True
    )
    """Timestamp detail; honoured only when the model has word timestamps."""

    def allowed_by(self, capabilities: ModeCapabilities) -> RequestedFeatures:
        """Apply the same capability constraints as the model options UI."""
        return RequestedFeatures(
            self.timestamps, self.diarization, self.timestamp_granularities
        ).allowed_by(capabilities)


def file_capabilities(model: ModelSpec) -> ModeCapabilities:
    """Upload options; a live-only model requests nothing beyond plain text."""
    return model.capabilities.file or ModeCapabilities()


class TranscriptionError(Exception):
    """A provider rejected or failed a request. The message is shown to the user."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class RealtimeSession(Protocol):
    """One SDK connection; reconnection and durable audio belong to the service layer."""

    async def send(self, pcm: bytes) -> None: ...
    async def flush(self) -> None: ...
    async def keep_alive(self) -> None: ...
    async def close(self) -> None: ...
    def events(self) -> AsyncIterator[TranscriptEvent]: ...


class TranscriptionProvider(Protocol):
    @property
    def max_upload_bytes(self) -> int | None:
        """Largest file :meth:`transcribe_file` accepts; longer audio must be split first."""
        ...

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        """Upload a finished recording and return its transcript."""
        ...

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits: ...

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession: ...
