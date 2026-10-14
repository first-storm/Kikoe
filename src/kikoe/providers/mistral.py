"""Mistral Voxtral transcription through its official asynchronous SDK."""

import asyncio
import json
import mimetypes
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx2 as httpx
from mistralai.client import Mistral
from mistralai.client.errors import MistralError, ResponseValidationError
from mistralai.client.models import AudioFormat, File
from mistralai.client.types import UNSET
from mistralai.extra.exceptions import RealtimeTranscriptionException

from kikoe.core.choice import RequestedFeatures, language_dropped
from kikoe.core.models import ApiKind, ModelSpec, Segment, TimestampGranularity
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
from kikoe.providers.errors import status_error
from kikoe.providers.mistral_file_stream import MistralFileTranscript
from kikoe.providers.mistral_parsing import parse_mistral
from kikoe.providers.mistral_realtime import MistralSession
from kikoe.providers.parsing import fields

MAX_UPLOAD_BYTES = 500_000_000
MAX_UPLOAD_SECONDS = 3600
"""Mistral's limits page says 60 minutes, its audio overview up to 3 hours; use the former."""
REQUEST_TIMEOUT_MS = 900_000
REALTIME_SAMPLE_RATE = 16000
REALTIME_CONNECT_TIMEOUT_MS = 30_000

type Granularity = Literal["segment", "word"]


@dataclass(frozen=True, slots=True)
class _Request:
    """The arguments shared by the complete and streaming transcription calls."""

    model: str
    file: File
    language: str | None
    diarize: bool
    granularities: list[Granularity]

    @property
    def words_only(self) -> bool:
        return self.granularities == ["word"]


class MistralClient:
    """Keep multipart encoding and transport lifetime inside the official SDK."""

    def __init__(
        self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = base_url.rstrip("/")
        self._host = urlsplit(base_url).hostname or base_url
        self._key = api_key
        self._transport = transport

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        return UploadLimits(MAX_UPLOAD_BYTES, timedelta(seconds=MAX_UPLOAD_SECONDS))

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
        request = _Request(
            model.model_id,
            File(
                file_name=audio.name,
                content=content,
                content_type=mimetypes.guess_type(audio.name)[0] or "application/octet-stream",
            ),
            _language(features, options.language),
            features.diarization,
            _granularities(features),
        )
        try:
            # The SDK never closes a client it was given, so this adapter owns its lifetime.
            async with (
                httpx.AsyncClient(transport=self._transport) as http,
                Mistral(
                    api_key=self._key,
                    server_url=self._url,
                    async_client=http,
                    timeout_ms=REQUEST_TIMEOUT_MS,
                ) as sdk,
            ):
                if caps.streaming:
                    return await _stream(sdk, request, features, on_update)
                return await _complete(sdk, request, features)
        except MistralError as error:
            raise self._error(error) from error
        except httpx.TimeoutException as error:
            raise self._unreachable(
                _("{host} took too long to respond. Try again later.")
            ) from error
        except httpx.TransportError as error:
            raise self._unreachable(_("Could not reach {host}. Try again later.")) from error
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise TranscriptionError(
                _("{host} sent a response Kikoe does not understand.").format(host=self._host)
            ) from error

    def _unreachable(self, message: str) -> TranscriptionError:
        return TranscriptionError(message.format(host=self._host), retryable=True)

    def _error(self, error: MistralError) -> TranscriptionError:
        if isinstance(error, ResponseValidationError):
            return TranscriptionError(
                _("{host} sent a response Kikoe does not understand.").format(host=self._host)
            )
        detail = _detail(error.body)
        if detail and self._key:
            detail = detail.replace(self._key, "[redacted]")
        return status_error(error.status_code, self._host, detail)

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        stack = AsyncExitStack()
        try:
            sdk = await stack.enter_async_context(
                Mistral(
                    api_key=self._key,
                    server_url=self._url,
                    timeout_ms=REALTIME_CONNECT_TIMEOUT_MS,
                )
            )
            connection = await sdk.audio.realtime.connect(
                model=model.model_id,
                audio_format=AudioFormat(encoding="pcm_s16le", sample_rate=REALTIME_SAMPLE_RATE),
            )
            stack.push_async_callback(connection.close)
            return MistralSession(connection, stack, self._key)
        except RealtimeTranscriptionException as error:
            await close_stack(stack)
            raise self._connect_error(error) from error
        except BaseException:
            await close_stack(stack)
            raise

    def _connect_error(self, error: RealtimeTranscriptionException) -> TranscriptionError:
        """The handshake failed; the SDK hides the HTTP status in the exception chain."""
        status = _handshake_status(error)
        if status is not None:
            return status_error(status, self._host)
        return self._unreachable(_("Could not reach {host}. Try again later."))


def _handshake_status(error: RealtimeTranscriptionException) -> int | None:
    """An HTTP status from the rejected upgrade, or from Mistral's own error event."""
    response: object = getattr(error.__cause__, "response", None)
    status: object = getattr(response, "status_code", None)
    if not isinstance(status, int):
        status = error.code
    return status if isinstance(status, int) and 400 <= status < 600 else None


async def _complete(
    sdk: Mistral, request: _Request, features: RequestedFeatures
) -> tuple[Segment, ...]:
    result = await sdk.audio.transcriptions.complete_async(
        model=request.model,
        file=request.file,
        language=request.language or UNSET,
        diarize=request.diarize,
        timestamp_granularities=request.granularities or None,
    )
    return parse_mistral(
        result.model_dump(warnings=False),
        features.timestamps,
        features.diarization,
        request.words_only,
    )


async def _stream(
    sdk: Mistral,
    request: _Request,
    features: RequestedFeatures,
    on_update: FileTranscriptCallback | None,
) -> tuple[Segment, ...]:
    transcript = MistralFileTranscript(
        timed=features.timestamps, diarize=features.diarization, words_only=request.words_only
    )
    stream = await sdk.audio.transcriptions.stream_async(
        model=request.model,
        file=request.file,
        language=request.language or UNSET,
        diarize=request.diarize,
        timestamp_granularities=request.granularities or None,
    )
    async with stream:
        async for event in stream:
            transcript.apply(event.data)
            if on_update is not None:
                on_update(transcript.snapshot)
            if transcript.finished:
                break
    return transcript.result()


def _language(features: RequestedFeatures, language: str | None) -> str | None:
    """Primary language code, unless Mistral cannot take it together with the options."""
    if not language or language_dropped(ApiKind.MISTRAL, features, language):
        return None
    return language.split("-")[0].lower()


def _granularities(features: RequestedFeatures) -> list[Granularity]:
    """Speakers only exist on segments, so they win over word-level times."""
    if features.diarization:
        return ["segment"]
    if not features.timestamps:
        return []
    return [
        "word" if g is TimestampGranularity.WORD else "segment"
        for g in features.timestamp_granularities
    ]


def _detail(body: str) -> str | None:
    """The service's own message; the SDK's exception text embeds the whole raw body."""
    try:
        message = fields(json.loads(body)).get("message")
    except ValueError, TypeError:
        return None
    return message.strip() or None if isinstance(message, str) else None
