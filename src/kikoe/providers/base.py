"""The interface every transcription backend implements."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar, Protocol
from urllib.parse import urlsplit

from kikoe.core.choice import RequestedFeatures
from kikoe.core.models import ModeCapabilities, ModelSpec, Segment
from kikoe.core.transcription import FileTranscriptSnapshot, RealtimeProfile, TranscriptEvent
from kikoe.core.upload import UploadLimits
from kikoe.i18n import _

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
class TranscriptionOptions(RequestedFeatures):
    """Per-request switches. Only honoured when the model's Capabilities allow them."""

    language: str | None = field(default=None, kw_only=True)
    """BCP 47 tag, or None to let the service detect it."""
    context_bias: tuple[str, ...] = field(default=(), kw_only=True)
    """Terms to favor; services without such a setting ignore them."""
    realtime_delay_ms: int | None = field(default=None, kw_only=True)
    """Live transcription delay; None keeps the service default."""


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


class HttpClient[T]:
    """Connection settings every SDK adapter keeps; ``T`` is the SDK's httpx transport type."""

    def __init__(self, base_url: str, api_key: str, transport: T | None = None) -> None:
        self._url = base_url.rstrip("/")
        self._host = urlsplit(base_url).hostname or base_url
        self._key = api_key
        self._transport = transport
        """Tests substitute a mock transport; None uses the network."""

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        """Endpoint caps live in the provider registry; override only for option limits."""
        return UploadLimits()

    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        return RealtimeProfile()


class FileOnlyClient[T](HttpClient[T]):
    """A service without live transcription."""

    name: ClassVar[str]
    """Brand name, shown when someone tries to transcribe live."""

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        # Translators: {name} is a service name such as Groq.
        message = _("{name} supports file transcription only.")
        raise TranscriptionError(message.format(name=self.name))


class TranscriptionProvider(Protocol):
    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        """Upload a finished recording and return its transcript.

        Callers split audio to ``upload_limits`` combined with the registry's
        ``UploadPolicy``, so clients do not check the size again.
        """
        ...

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits: ...

    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        """How live audio must be delivered; file-only services return the default."""
        ...

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession: ...
