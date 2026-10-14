"""Xiaomi MiMo speech recognition, which takes audio inside a chat completion request."""

import asyncio
import base64
from pathlib import Path
from typing import cast

import httpx2 as httpx
from openai import APIError
from openai.types.chat import ChatCompletionMessageParam

from kikoe.core.models import ModelSpec, Segment
from kikoe.core.upload import WAV
from kikoe.i18n import _
from kikoe.providers.base import (
    FileOnlyClient,
    FileTranscriptCallback,
    TranscriptionError,
    TranscriptionOptions,
)
from kikoe.providers.errors import openai_error
from kikoe.providers.openai import openai_sdk


def asr_language(tag: str | None) -> str:
    """MiMo knows Chinese and English; anything else is left to automatic detection."""
    primary = (tag or "").split("-")[0].lower()
    return primary if primary in ("zh", "en") else "auto"


class MiMoClient(FileOnlyClient[httpx.AsyncBaseTransport]):
    """Send one WAV per request and return its plain text; MiMo gives no timestamps."""

    name = "MiMo"

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        content = await asyncio.to_thread(audio.read_bytes)
        # WAV is the only upload format the provider registry allows for MiMo.
        data = f"data:{WAV.mime};base64,{base64.b64encode(content).decode('ascii')}"
        try:
            async with openai_sdk(self._url, self._key, self._transport) as sdk:
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
