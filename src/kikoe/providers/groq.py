"""Groq speech transcription through its official asynchronous SDK."""

import asyncio
import mimetypes
from pathlib import Path

import httpx
from groq import APIError, AsyncGroq, omit

from kikoe.core.models import ModelSpec, Segment
from kikoe.providers.base import (
    FileOnlyClient,
    FileTranscriptCallback,
    TranscriptionOptions,
    file_capabilities,
)
from kikoe.providers.errors import openai_error, unreadable
from kikoe.providers.parsing import parse_segments


class GroqClient(FileOnlyClient[httpx.AsyncBaseTransport]):
    """Keep multipart encoding and transport lifetime inside the official SDK."""

    name = "Groq"

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
        mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        try:
            async with AsyncGroq(
                api_key=self._key,
                base_url=self._url,
                max_retries=0,
                timeout=httpx.Timeout(30, read=900, write=300),
                http_client=httpx.AsyncClient(transport=self._transport),
            ) as sdk:
                result = await sdk.audio.transcriptions.create(
                    file=(audio.name, content, mime),
                    model=model.model_id,
                    language=options.language.split("-")[0].lower() if options.language else omit,
                    response_format="verbose_json" if features.timestamps else "json",
                    timestamp_granularities=["segment"] if features.timestamps else omit,
                )
                # Verbose fields are preserved as SDK extras, although only text is typed.
                # Parsing validates text and times; serializer warnings could expose payloads.
                return parse_segments(result.model_dump(warnings=False), features.timestamps, False)
        except APIError as error:
            raise openai_error(error, self._host, self._key) from error
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise unreadable(self._host) from error
