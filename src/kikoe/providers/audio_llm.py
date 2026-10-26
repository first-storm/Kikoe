"""Prompted transcription by any chat model that accepts audio through chat completions."""

import asyncio
import base64
from functools import partial
from pathlib import Path
from typing import Literal

import httpx2 as httpx
from openai import APIError, omit

from kikoe.core.llm_prompt import render_prompt
from kikoe.core.models import LlmSettings, ModelSpec, Segment
from kikoe.core.upload import MP3, upload_format_of
from kikoe.i18n import _
from kikoe.providers.base import (
    FileOnlyClient,
    FileTranscriptCallback,
    TranscriptionError,
    TranscriptionOptions,
    consume_file_stream,
    file_capabilities,
)
from kikoe.providers.chat_file_stream import ChatFileTranscript, completed_text
from kikoe.providers.errors import openai_error, unreadable
from kikoe.providers.openai import openai_sdk


class AudioLlmClient(FileOnlyClient[httpx.AsyncBaseTransport]):
    """Send one MP3 or WAV and the provider's prompt per request; the reply is plain text."""

    name = "Audio LLM"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        llm: LlmSettings,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, api_key, transport)
        self._llm = llm

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        content = await asyncio.to_thread(audio.read_bytes)
        # input_audio takes only these two, which is all the provider registry allows.
        audio_format: Literal["mp3", "wav"] = "mp3" if upload_format_of(audio) == MP3 else "wav"
        data = base64.b64encode(content).decode("ascii")
        prompt = render_prompt(self._llm, options.language)
        try:
            # Local servers such as vLLM may deliberately run without a key.
            async with openai_sdk(self._url, self._key, self._transport, require_key=False) as sdk:
                create = partial(
                    sdk.chat.completions.create,
                    model=model.model_id,
                    # The SDK refuses to send a chat request with neither a key nor this.
                    extra_headers=None if self._key else {"Authorization": omit},
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "input_audio",
                                    "input_audio": {"data": data, "format": audio_format},
                                },
                            ],
                        }
                    ],
                )
                if file_capabilities(model).streaming:
                    transcript = ChatFileTranscript(self._host, allow_empty=True)
                    stream = await create(stream=True)
                    async with stream:
                        return await consume_file_stream(stream, transcript, on_update)
                completion = await create(stream=False)
                if not completion.choices:
                    raise unreadable(self._host)
                choice = completion.choices[0]
                return completed_text(
                    choice.message.content, choice.finish_reason, self._host, allow_empty=True
                )
        except APIError as error:
            raise openai_error(error, self._host, self._key) from error
        except httpx.HTTPError as error:
            # The SDK exposes transport errors directly while iterating an SSE response.
            raise TranscriptionError(
                _("Could not reach {host}. Try again later.").format(host=self._host),
                retryable=True,
            ) from error
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise unreadable(self._host) from error
