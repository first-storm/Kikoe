"""Official asynchronous OpenAI SDK, also used for compatible file endpoints."""

import asyncio
import base64
import mimetypes
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack
from datetime import timedelta
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx
from openai import APIError, AsyncOpenAI, omit
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from openai.types.realtime.audio_transcription_param import AudioTranscriptionParam
from pydantic import BaseModel

from kikoe.core.models import ModelSpec, Segment
from kikoe.core.recording import pcm_level
from kikoe.core.transcription import RealtimeProfile, SpeechBoundary, TranscriptEvent
from kikoe.core.upload import UploadLimits
from kikoe.i18n import _
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
    close_stack,
    file_capabilities,
)
from kikoe.providers.errors import openai_error
from kikoe.providers.openai_file_stream import OpenAIFileTranscript
from kikoe.providers.parsing import parse_segments

MAX_UPLOAD_BYTES = 25_000_000


class OpenAIClient:
    """Keep authentication, retries and multipart encoding inside the official SDK."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/")
        self._host = urlsplit(base_url).hostname or base_url
        self._key = api_key
        self._transport = transport

    def _sdk(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self._key,
            base_url=self._url,
            max_retries=0,
            _enforce_credentials=False,  # Compatible local servers may deliberately have no key.
            timeout=httpx.Timeout(30, read=900, write=300),
            http_client=httpx.AsyncClient(transport=self._transport),
        )

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        return UploadLimits(MAX_UPLOAD_BYTES)

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        if (await asyncio.to_thread(audio.stat)).st_size > MAX_UPLOAD_BYTES:
            raise TranscriptionError(_("The audio file is too large to upload to this provider."))
        content = await asyncio.to_thread(audio.read_bytes)
        caps = file_capabilities(model)
        features = options.allowed_by(caps)
        timed, diarize = features.timestamps, features.diarization
        # Diarizing models return speakers and times only in diarized_json.
        structured = caps.diarization and (diarize or timed)
        mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        try:
            result: object
            async with self._sdk() as sdk:
                create = partial(
                    sdk.audio.transcriptions.create,
                    file=(audio.name, content, mime),
                    model=model.model_id,
                    language=options.language or omit,
                    extra_headers={"Authorization": omit} if not self._key else None,
                )
                if caps.streaming:
                    transcript = OpenAIFileTranscript(
                        structured=structured, timed=timed, diarize=diarize
                    )
                    stream = await create(
                        response_format="diarized_json" if structured else "json",
                        stream=True,
                        chunking_strategy="auto" if caps.diarization else omit,
                    )
                    async with stream:
                        async for event in stream:
                            transcript.apply(event)
                            if on_update is not None:
                                on_update(transcript.snapshot)
                            if event.type == "transcript.text.done":
                                break
                    return transcript.result()
                if structured:
                    result = await create(
                        response_format="diarized_json", stream=False, chunking_strategy="auto"
                    )
                else:
                    result = await create(
                        response_format="verbose_json" if timed else "json",
                        timestamp_granularities=["segment"] if timed else omit,
                    )
                if not isinstance(result, BaseModel):
                    raise ValueError("Unexpected streaming result")
                return parse_segments(result.model_dump(), timed, diarize)
        except APIError as error:
            raise openai_error(error, self._host, self._key) from error
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise TranscriptionError(
                _("{host} sent a response Kikoe does not understand.").format(host=self._host)
            ) from error

    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        return RealtimeProfile(sample_rate=24000)

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        stack = AsyncExitStack()
        try:
            sdk = await stack.enter_async_context(self._sdk())
            connection = await stack.enter_async_context(
                sdk.realtime.connect(extra_query={"intent": "transcription"}, max_retries=0)
            )
            transcription: AudioTranscriptionParam = {"model": model.model_id}
            if options.language:
                if model.model_id == "gpt-live-transcribe":
                    transcription["languages"] = [options.language]
                else:
                    transcription["language"] = options.language
            # send() is the SDK's escape hatch for model-specific session configuration.
            await connection.send(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": transcription,
                                "turn_detection": None,
                            }
                        },
                    },
                }
            )
            async with asyncio.timeout(30):
                async for event in connection:
                    if event.type == "session.updated":
                        break
                    if event.type == "error":
                        raise TranscriptionError(
                            _("{host} rejected the realtime session: {message}").format(
                                host=self._host, message=event.error.message
                            )
                        )
            return OpenAISession(connection, stack)
        except BaseException:
            await close_stack(stack)
            raise


class OpenAISession:
    """Match completed items to commit order, not network completion order."""

    def __init__(self, connection: AsyncRealtimeConnection, stack: AsyncExitStack) -> None:
        self._connection = connection
        self._stack = stack
        self._order: list[str] = []
        self._completed: dict[str, str] = {}
        self._previews: dict[str, str] = {}
        self._commits = 0
        self._finals = 0
        self._bytes = 0
        self._changed = asyncio.Event()
        self._boundary = SpeechBoundary()

    async def send(self, pcm: bytes) -> None:
        await self._connection.input_audio_buffer.append(audio=base64.b64encode(pcm).decode())
        self._bytes += len(pcm)
        if self._boundary.feed(timedelta(seconds=len(pcm) / 48000), pcm_level(pcm)):
            await self.flush()

    async def flush(self) -> None:
        if not self._bytes:
            return
        if self._bytes < 4800:
            await self._connection.input_audio_buffer.append(
                audio=base64.b64encode(bytes(4800 - self._bytes)).decode()
            )
        self._bytes = 0
        self._commits += 1
        await self._connection.input_audio_buffer.commit()
        while self._finals < self._commits:
            self._changed.clear()
            if self._finals < self._commits:
                await self._changed.wait()

    async def keep_alive(self) -> None:
        pass  # The SDK maintains WebSocket ping/pong during microphone pauses.

    async def close(self) -> None:
        await close_stack(self._stack)

    async def events(self) -> AsyncGenerator[TranscriptEvent]:
        async for event in self._connection:
            match event.type:
                case "input_audio_buffer.committed":
                    if event.item_id not in self._order:
                        self._order.append(event.item_id)
                case "conversation.item.input_audio_transcription.delta":
                    text = self._previews.get(event.item_id, "") + (event.delta or "")
                    self._previews[event.item_id] = text
                    yield TranscriptEvent(event.item_id, (Segment(text),), False)
                case "conversation.item.input_audio_transcription.completed":
                    self._completed[event.item_id] = event.transcript
                case "error" | "conversation.item.input_audio_transcription.failed":
                    raise TranscriptionError(
                        _("Realtime transcription failed. Your audio has been kept."),
                        retryable=False,
                    )
                case _:
                    pass
            while self._order and self._order[0] in self._completed:
                id = self._order.pop(0)
                text = self._completed.pop(id).strip()
                self._previews.pop(id, None)
                yield TranscriptEvent(id, (Segment(text),) if text else (), True)
                self._finals += 1
                self._changed.set()
