"""File transcription through OpenRouter's official asynchronous STT SDK."""

import asyncio
from pathlib import Path

import httpx
from openrouter import OpenRouter

from kikoe.core.models import ModelSpec, Segment
from kikoe.providers.base import (
    FileOnlyClient,
    FileTranscriptCallback,
    TranscriptionOptions,
    file_capabilities,
)
from kikoe.providers.errors import sdk_error
from kikoe.providers.parsing import parse_segments, parse_words


class OpenRouterClient(FileOnlyClient[httpx.AsyncBaseTransport]):
    """Request STT metadata through the SDK's multipart endpoint."""

    name = "OpenRouter"

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        content = await asyncio.to_thread(audio.read_bytes)
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
