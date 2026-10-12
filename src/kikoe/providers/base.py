"""The interface every transcription backend implements."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Protocol

from kikoe.core.models import ModelSpec, Segment, TimestampGranularity
from kikoe.core.transcription import TranscriptEvent

log = logging.getLogger(__name__)


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
    """Timestamp detail; only OpenRouter honours the selection for now."""


class TranscriptionError(Exception):
    """A provider rejected or failed a request. The message is shown to the user."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """Limits depend on the model and requested metadata, not only the provider."""

    bytes: int | None = None
    duration: timedelta | None = None


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
        self, audio: Path, model: ModelSpec, options: TranscriptionOptions
    ) -> tuple[Segment, ...]:
        """Upload a finished recording and return its transcript."""
        ...

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits: ...

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession: ...
