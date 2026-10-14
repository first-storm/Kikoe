"""Xiaomi MiMo speech recognition, which takes audio inside a chat completion request."""

import asyncio
import base64
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx2 as httpx
from openai import APIError, AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from kikoe.core.models import ModelSpec, Segment
from kikoe.core.transcription import RealtimeProfile
from kikoe.core.upload import MP3, WAV, UploadLimits
from kikoe.i18n import _
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
)
from kikoe.providers.errors import openai_error

MAX_UPLOAD_BYTES = 7_500_000
"""10 MB of Base64 holds at most 7.5 MB of raw audio."""


def asr_language(tag: str | None) -> str:
    """MiMo knows Chinese and English; anything else is left to automatic detection."""
    primary = (tag or "").split("-")[0].lower()
    return primary if primary in ("zh", "en") else "auto"


class MiMoClient:
    """Send one MP3 per request and return its plain text; MiMo gives no timestamps."""

    def __init__(
        self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = base_url.rstrip("/")
        self._host = urlsplit(base_url).hostname or base_url
        self._key = api_key
        self._transport = transport

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        return UploadLimits(MAX_UPLOAD_BYTES, format=MP3)

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
        mime = WAV.mime if audio.suffix.lower() == WAV.suffix else MP3.mime
        data = f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
        try:
            async with AsyncOpenAI(
                api_key=self._key,
                base_url=self._url,
                max_retries=0,
                timeout=httpx.Timeout(30, read=900, write=300),
                http_client=httpx.AsyncClient(transport=self._transport),
            ) as sdk:
                completion = await sdk.chat.completions.create(
                    model=model.model_id,
                    # MiMo takes a Data URL without the SDK's `format` field, so the typed
                    # input_audio param does not fit.
                    messages=cast(
                        list[ChatCompletionMessageParam],
                        [
                            {
                                "role": "user",
                                "content": [{"type": "input_audio", "input_audio": {"data": data}}],
                            }
                        ],
                    ),
                    extra_body={"asr_options": {"language": asr_language(options.language)}},
                )
        except APIError as error:
            raise openai_error(error, self._host, self._key) from error
        text = (completion.choices[0].message.content or "").strip() if completion.choices else ""
        if not text:
            raise TranscriptionError(
                _("{host} sent a response Kikoe does not understand.").format(host=self._host)
            )
        return (Segment(text),)

    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        return RealtimeProfile()

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        raise TranscriptionError(_("MiMo supports file transcription only."))
