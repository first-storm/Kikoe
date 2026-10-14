"""File transcription through OpenRouter's official asynchronous STT SDK."""

import asyncio
from pathlib import Path

import httpx
from openrouter import OpenRouter

from kikoe.core.models import ModelSpec, Segment
from kikoe.core.upload import UploadLimits
from kikoe.i18n import _
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
    file_capabilities,
)
from kikoe.providers.errors import sdk_error
from kikoe.providers.parsing import parse_segments, parse_words

MAX_UPLOAD_BYTES = 25_000_000


class OpenRouterClient:
    """Request STT metadata through the SDK's multipart endpoint."""

    def __init__(
        self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = base_url.rstrip("/")
        self._key = api_key
        self._transport = transport

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
        content = await asyncio.to_thread(audio.read_bytes)
        if len(content) > 25_000_000:
            raise TranscriptionError(_("The audio file is too large to upload to this provider."))
        caps = file_capabilities(model)
        features = options.allowed_by(caps)
        timed, diarize = features.timestamps, features.diarization
        granularities = features.timestamp_granularities
        try:
            async with (
                httpx.AsyncClient(transport=self._transport) as http,
                OpenRouter(api_key=self._key, server_url=self._url, async_client=http) as sdk,
            ):
                result = await sdk.stt.create_transcription_multipart_async(
                    model=model.model_id,
                    file={"file_name": audio.name, "content": content},
                    language=options.language,
                    diarize=diarize,
                    response_format="verbose_json" if timed or diarize else "json",
                    timestamp_granularities=([g.value for g in granularities] if timed else None),
                )
                data = result.model_dump()
                # OpenRouter attaches speaker identity to words, not every segment.
                if diarize and result.words:
                    return parse_words(data.get("words", []), timed, True)
                return parse_segments(data, timed, diarize)
        except Exception as error:
            raise sdk_error(error) from error

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        raise TranscriptionError(_("OpenRouter supports file transcription only."))
