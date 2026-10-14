"""Official Deepgram SDK adapters for Nova file and Listen v1 live transcription."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlsplit, urlunsplit

import httpx
from deepgram import AsyncDeepgramClient
from deepgram.environment import DeepgramClientEnvironment
from pydantic import BaseModel

from kikoe.core.models import ModelSpec, Segment
from kikoe.core.transcription import CheckpointStrategy, RealtimeProfile, TranscriptEvent
from kikoe.i18n import _
from kikoe.providers.base import (
    FileTranscriptCallback,
    HttpClient,
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
    close_stack,
    file_capabilities,
)
from kikoe.providers.errors import sdk_error
from kikoe.providers.parsing import entries, fields, parse_words, seconds


class _Socket(Protocol):
    def __aiter__(self) -> AsyncIterator[object]: ...
    async def send_media(self, message: bytes) -> None: ...
    async def send_close_stream(self) -> None: ...
    async def send_keep_alive(self) -> None: ...


class _Media(Protocol):
    async def transcribe_file(
        self,
        *,
        request: bytes,
        model: str,
        language: str | None,
        detect_language: bool,
        smart_format: bool,
        utterances: bool,
        diarize_model: str | None,
    ) -> BaseModel: ...


class _Listen(Protocol):
    media: _Media

    def connect(
        self,
        *,
        model: str,
        encoding: str,
        sample_rate: int,
        channels: int,
        language: str,
        interim_results: str,
        endpointing: int,
        punctuate: str,
        diarize_model: str | None,
    ) -> AbstractAsyncContextManager[_Socket]: ...


class DeepgramClient(HttpClient[httpx.AsyncBaseTransport]):
    """Type the generated SDK's unannotated accessors at the adapter boundary."""

    def _listen(self, http: httpx.AsyncClient) -> _Listen:
        base = self._url.removesuffix("/v1")  # The SDK adds the API version itself.
        parts = urlsplit(base)
        ws = urlunsplit(parts._replace(scheme="wss" if parts.scheme == "https" else "ws"))
        sdk = AsyncDeepgramClient(
            api_key=self._key,
            reconnect=False,
            max_retries=0,
            httpx_client=http,
            environment=DeepgramClientEnvironment(
                base=base, production=ws, agent=ws, agent_rest=base
            ),
        )
        return cast(_Listen, sdk.listen.v1)

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        content = await asyncio.to_thread(audio.read_bytes)
        features = options.allowed_by(file_capabilities(model))
        try:
            async with httpx.AsyncClient(timeout=900, transport=self._transport) as http:
                result = await self._listen(http).media.transcribe_file(
                    request=content,
                    model=model.model_id,
                    language=options.language,
                    detect_language=options.language is None,
                    smart_format=True,
                    utterances=True,
                    diarize_model="latest" if features.diarization else None,
                )
                return parse_deepgram(
                    result.model_dump(),
                    features.timestamps,
                    features.diarization,
                )
        except Exception as error:
            raise sdk_error(error) from error

    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        return RealtimeProfile(checkpoint=CheckpointStrategy.AUDIO_END)

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        stack = AsyncExitStack()
        try:
            http = await stack.enter_async_context(httpx.AsyncClient(timeout=30))
            socket = await stack.enter_async_context(
                self._listen(http).connect(
                    model=model.model_id,
                    encoding="linear16",
                    sample_rate=16000,
                    channels=1,
                    language=options.language or "multi",
                    interim_results="true",
                    endpointing=300,
                    punctuate="true",
                    diarize_model="v1" if options.diarization else None,
                )
            )
            return DeepgramSession(socket, stack, options)
        except BaseException:
            await close_stack(stack)
            raise


def parse_deepgram(body: object, timed: bool, diarize: bool) -> tuple[Segment, ...]:
    result = fields(fields(body)["results"])
    channels = entries(result.get("channels"))
    if not channels:
        raise ValueError("Missing channels")
    alternatives = entries(fields(channels[0]).get("alternatives"))
    if not alternatives:
        raise ValueError("Missing alternatives")
    return _alternative(fields(alternatives[0]), timed, diarize)


def _alternative(data: dict[str, object], timed: bool, diarize: bool) -> tuple[Segment, ...]:
    words = entries(data.get("words"))
    if words:
        return parse_words(words, timed, diarize)
    value = data.get("transcript")
    if not isinstance(value, str):
        raise ValueError("Missing transcript")
    text = value.strip()
    return (Segment(text),) if text else ()


class DeepgramSession:
    """Finalize acknowledgements allow the service to checkpoint accepted audio."""

    def __init__(
        self, socket: _Socket, stack: AsyncExitStack, options: TranscriptionOptions
    ) -> None:
        self._socket = socket
        self._stack = stack
        self._options = options
        self._drained = asyncio.Event()
        self._dirty = False

    async def send(self, pcm: bytes) -> None:
        self._dirty = True
        await self._socket.send_media(pcm)

    async def flush(self) -> None:
        if self._dirty:
            self._drained.clear()
            # CloseStream guarantees terminal Metadata even for an all-silent recording.
            await self._socket.send_close_stream()
            await self._drained.wait()
            self._dirty = False

    async def keep_alive(self) -> None:
        await self._socket.send_keep_alive()

    async def close(self) -> None:
        await close_stack(self._stack)

    async def events(self) -> AsyncGenerator[TranscriptEvent]:
        async for message in self._socket:
            if not isinstance(message, BaseModel):
                continue
            data = fields(message.model_dump())
            if data.get("type") == "Metadata":
                self._drained.set()
            if data.get("type") == "Error":
                reason = data.get("description") or data.get("message")
                if isinstance(reason, str) and reason:
                    raise TranscriptionError(
                        _("Deepgram rejected realtime transcription: {message}").format(
                            message=reason
                        )
                    )
                raise TranscriptionError(
                    _("The provider rejected realtime transcription. Check the model and API key.")
                )
            if data.get("type") != "Results":
                continue
            alternatives = entries(fields(data["channel"]).get("alternatives"))
            if alternatives:
                segments = _alternative(
                    fields(alternatives[0]), self._options.timestamps, self._options.diarization
                )
                begin = seconds(data.get("start"))
                duration = seconds(data.get("duration"))
                audio_end = begin + duration if begin is not None and duration is not None else None
                yield TranscriptEvent(
                    str(data.get("start", 0)), segments, data.get("is_final") is True, audio_end
                )
            if data.get("from_finalize") is True:
                self._drained.set()
