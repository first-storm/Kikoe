"""Gemini AI Studio transcription through the official Google Gen AI SDK."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AsyncExitStack
from datetime import timedelta
from pathlib import Path
from typing import Protocol, cast

import httpx
from google import genai
from google.genai import types
from pydantic import BaseModel

from kikoe.core.models import ModelSpec, Segment
from kikoe.core.transcription import TranscriptEvent
from kikoe.core.upload import UploadLimits
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionOptions,
    close_stack,
    file_capabilities,
)
from kikoe.providers.errors import sdk_error
from kikoe.providers.parsing import entries, fields, parse_words
from kikoe.providers.worker import run_sdk_worker

FLUSH_GRACE_SECONDS = 2
"""How long a flush waits for speech the server has not announced yet."""


class _LiveSession(Protocol):
    async def send_realtime_input(
        self, *, audio: types.Blob | None = None, audio_stream_end: bool | None = None
    ) -> None: ...
    def receive(self) -> AsyncIterator[types.LiveServerMessage]: ...


class GeminiClient:
    """Use the specialized verbatim STT model, not a prompted generative transcript."""

    max_upload_bytes: int | None = None

    def __init__(
        self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = base_url.rstrip("/")
        self._key = api_key
        self._transport = transport

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

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        features = options.allowed_by(file_capabilities(model))
        annotated = features.timestamps or features.diarization
        return UploadLimits(duration=timedelta(minutes=30 if annotated else 60))

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        # The SDK's resumable-upload retry path uses blocking sleeps internally.
        # Isolate its loop so a malformed server response cannot freeze GTK.
        return await run_sdk_worker(lambda: self._transcribe_file(audio, model, options))

    async def _transcribe_file(
        self, audio: Path, model: ModelSpec, options: TranscriptionOptions
    ) -> tuple[Segment, ...]:
        caps = file_capabilities(model)
        features = options.allowed_by(caps)
        timed, diarize = features.timestamps, features.diarization
        try:
            async with self._sdk().aio as sdk:
                uploaded = await sdk.files.upload(file=audio)
                try:
                    mode: dict[str, object] = {"type": "verbatim"}
                    if timed:
                        mode["timestamp_granularities"] = ["word"]
                    if diarize:
                        mode["diarization_mode"] = "speaker"
                    result: object = await sdk.interactions.create(
                        model=model.model_id,
                        input=[
                            {"type": "audio", "uri": uploaded.uri, "mime_type": uploaded.mime_type}
                        ],
                        generation_config={
                            "transcription_config": {
                                "language_codes": [options.language] if options.language else [],
                                "mode": mode,
                            }
                        },
                    )
                    if not isinstance(result, BaseModel):
                        raise ValueError("Unexpected streaming response")
                    return parse_gemini(result.model_dump(), timed, diarize)
                finally:
                    if uploaded.name:
                        await sdk.files.delete(name=uploaded.name)
        except Exception as error:
            raise sdk_error(error) from error

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        stack = AsyncExitStack()
        try:
            sdk = await stack.enter_async_context(self._sdk().aio)
            session = await stack.enter_async_context(
                sdk.live.connect(
                    model=model.model_id,
                    config=types.LiveConnectConfig(
                        response_modalities=[types.Modality.TEXT],
                        input_audio_transcription=types.AudioTranscriptionConfig(
                            language_codes=[options.language] if options.language else []
                        ),
                    ),
                )
            )
            return GeminiSession(cast(_LiveSession, session), stack)
        except BaseException:
            await close_stack(stack)
            raise


def parse_gemini(body: object, timed: bool, diarize: bool) -> tuple[Segment, ...]:
    data = fields(body)
    words: list[object] = []
    for step in entries(data.get("steps")):
        for content in entries(fields(step).get("content")):
            output = fields(content).get("text")
            for annotation in entries(fields(content).get("annotations")):
                word = fields(annotation)
                if word.get("type") != "word_info":
                    continue
                begin, end = word.get("start_index"), word.get("end_index")
                if (
                    not word.get("text")
                    and isinstance(output, str)
                    and isinstance(begin, int)
                    and isinstance(end, int)
                ):
                    word = dict(word, text=output.encode("utf-8")[begin:end].decode("utf-8"))
                words.append(word)
    if words:
        segments = parse_words(words, timed, diarize)
        if segments:
            return segments
    text = data.get("output_text")
    if not isinstance(text, str):
        raise ValueError("Missing transcript")
    return (Segment(text.strip()),) if text.strip() else ()


class GeminiSession:
    """Keep receiving after each completed turn; each receive iterator is finite."""

    def __init__(self, session: _LiveSession, stack: AsyncExitStack) -> None:
        self._session = session
        self._stack = stack
        self._drained = asyncio.Event()
        self._dirty = False
        self._active = False
        self._closed = False
        self._id = 0

    async def send(self, pcm: bytes) -> None:
        self._dirty = True
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type="audio/pcm;rate=16000")
        )

    async def flush(self) -> None:
        if self._dirty:
            self._drained.clear()
            await self._session.send_realtime_input(audio_stream_end=True)
            await self._wait_drained()
            self._dirty = False

    async def _wait_drained(self) -> None:
        """Wait for the final transcript, unless the server has nothing left to finish.

        The server detects speech itself and finishes each utterance on its own, so the
        stream end of a silent or already finished stretch is never answered.
        """
        if self._active:
            await self._drained.wait()
            return
        try:
            async with asyncio.timeout(FLUSH_GRACE_SECONDS):
                await self._drained.wait()
        except TimeoutError:
            pass

    async def keep_alive(self) -> None:
        pass  # WebSocket ping/pong is maintained by the SDK.

    async def close(self) -> None:
        self._closed = True
        await close_stack(self._stack)

    async def events(self) -> AsyncGenerator[TranscriptEvent]:
        while not self._closed:
            async for response in self._session.receive():
                if (
                    response.voice_activity
                    and response.voice_activity.voice_activity_type
                    == types.VoiceActivityType.ACTIVITY_START
                ):
                    self._active = True
                content = response.server_content
                if content is None:
                    if response.go_away:
                        raise ConnectionError("Gemini session is expiring")
                    continue
                interim = content.interim_input_transcription
                if interim and interim.text:
                    yield TranscriptEvent(str(self._id), (Segment(interim.text),), False)
                final = content.input_transcription
                if final and final.text:
                    yield TranscriptEvent(str(self._id), (Segment(final.text),), True)
                    self._id += 1
                # The transcribe-live model ends a flush with generation_complete and never
                # sends turn_complete, so waiting only for that made every stop time out.
                if (
                    content.generation_complete
                    or content.turn_complete
                    or content.interaction_status == "IDLE"
                ):
                    self._active = False
                    self._drained.set()
