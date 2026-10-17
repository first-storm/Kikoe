"""Prompted transcription by a Gemini model through ``generateContent`` with inline audio."""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol, cast

import httpx
from google import genai
from google.genai import types

from kikoe.core.llm_prompt import render_prompt
from kikoe.core.models import LlmSettings, ModelSpec, Segment
from kikoe.core.upload import upload_format_of
from kikoe.providers.base import (
    FileOnlyClient,
    FileTranscriptCallback,
    TranscriptionError,
    TranscriptionOptions,
    file_capabilities,
)
from kikoe.providers.chat_file_stream import ChatFileTranscript
from kikoe.providers.errors import sdk_error

_FILTERED = frozenset(
    {
        types.FinishReason.SAFETY,
        types.FinishReason.RECITATION,
        types.FinishReason.BLOCKLIST,
        types.FinishReason.PROHIBITED_CONTENT,
        types.FinishReason.SPII,
    }
)


class _Models(Protocol):
    """The part of ``AsyncModels`` used here; its full signature mentions untyped PIL images."""

    async def generate_content(
        self, *, model: str, contents: list[types.Part]
    ) -> types.GenerateContentResponse: ...

    async def generate_content_stream(
        self, *, model: str, contents: list[types.Part]
    ) -> AsyncIterator[types.GenerateContentResponse]: ...


def chat_finish_reason(response: types.GenerateContentResponse) -> str | None:
    """Translate Gemini's finish reason to the OpenAI name ``completed_text`` expects."""
    if not response.candidates:
        feedback = response.prompt_feedback
        return "content_filter" if feedback and feedback.block_reason else None
    reason = response.candidates[0].finish_reason
    match reason:
        case None:
            return None
        case types.FinishReason.STOP:
            return "stop"
        case types.FinishReason.MAX_TOKENS:
            return "length"
        case _ if reason in _FILTERED:
            return "content_filter"
        case _:
            return reason.value


class AudioLlmGeminiClient(FileOnlyClient[httpx.AsyncBaseTransport]):
    """Send one chunk inline with the provider's prompt; the reply is plain text."""

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

    def _sdk(self) -> genai.Client:
        return genai.Client(
            api_key=self._key,
            vertexai=False,
            http_options=types.HttpOptions(
                base_url=self._url,
                async_client_args={"transport": self._transport}
                if self._transport is not None
                else None,
            ),
        )

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        content = await asyncio.to_thread(audio.read_bytes)
        contents = [
            types.Part.from_bytes(data=content, mime_type=upload_format_of(audio).mime),
            types.Part.from_text(text=render_prompt(self._llm, options.language)),
        ]
        transcript = ChatFileTranscript(self._host, allow_empty=True)
        try:
            async with self._sdk().aio as sdk:
                models = cast(_Models, sdk.models)
                if not file_capabilities(model).streaming:
                    response = await models.generate_content(
                        model=model.model_id, contents=contents
                    )
                    transcript.add(response.text or "", chat_finish_reason(response))
                    return transcript.result()
                stream = await models.generate_content_stream(
                    model=model.model_id, contents=contents
                )
                async for chunk in stream:
                    changed = transcript.add(chunk.text or "", chat_finish_reason(chunk))
                    if changed and on_update is not None:
                        on_update(transcript.snapshot)
                    if transcript.finished:
                        break
                return transcript.result()
        except TranscriptionError:
            raise
        except Exception as error:
            raise sdk_error(error) from error
