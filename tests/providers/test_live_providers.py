"""SDK event streams finalize utterances without confusing previews or commit order."""

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import cast

import pytest
from google.genai import types
from openai.resources.realtime.realtime import AsyncRealtimeConnection
from openai.types.realtime.conversation_item_input_audio_transcription_completed_event import (
    ConversationItemInputAudioTranscriptionCompletedEvent,
    UsageTranscriptTextUsageDuration,
)
from openai.types.realtime.input_audio_buffer_committed_event import InputAudioBufferCommittedEvent
from openai.types.realtime.realtime_server_event import RealtimeServerEvent
from pydantic import BaseModel

from kikoe.core.models import Segment
from kikoe.providers.base import TranscriptionOptions
from kikoe.providers.deepgram import DeepgramSession
from kikoe.providers.gemini import GeminiSession
from kikoe.providers.openai import OpenAISession
from kikoe.providers.worker import run_sdk_worker
from tests.helpers import run_async


class OpenAISocket:
    def __aiter__(self) -> AsyncIterator[RealtimeServerEvent]:
        return self.events()

    async def events(self) -> AsyncIterator[RealtimeServerEvent]:
        for id, previous in [("a", ""), ("b", "a")]:
            yield InputAudioBufferCommittedEvent(
                type="input_audio_buffer.committed",
                event_id=id,
                item_id=id,
                previous_item_id=previous,
            )
        for id, text in [("b", "Second"), ("a", "First")]:
            yield ConversationItemInputAudioTranscriptionCompletedEvent(
                type="conversation.item.input_audio_transcription.completed",
                event_id=id,
                item_id=id,
                content_index=0,
                transcript=text,
                usage=UsageTranscriptTextUsageDuration(type="duration", seconds=1),
            )


@run_async
async def test_openai_completion_events_are_emitted_in_audio_commit_order() -> None:
    session = OpenAISession(cast(AsyncRealtimeConnection, OpenAISocket()), AsyncExitStack())
    events = [event async for event in session.events()]
    assert [e.segments for e in events] == [(Segment("First"),), (Segment("Second"),)]
    assert all(e.final for e in events)


class Message(BaseModel):
    type: str
    start: float = 0
    duration: float = 1
    is_final: bool = False
    speech_final: bool = False
    from_finalize: bool = False
    channel: dict[str, object] = {}


class DeepgramSocket:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[object] = asyncio.Queue()
        self.commands: list[str] = []

    def __aiter__(self) -> AsyncIterator[object]:
        return self.events()

    async def events(self) -> AsyncIterator[object]:
        while True:
            yield await self.messages.get()

    async def send_media(self, message: bytes) -> None:
        pass

    async def send_close_stream(self) -> None:
        self.commands.append("CloseStream")
        await self.messages.put(Message(type="Metadata"))

    async def send_keep_alive(self) -> None:
        self.commands.append("KeepAlive")


@run_async
async def test_deepgram_saves_final_chunks_before_speech_final() -> None:
    socket = DeepgramSocket()
    session = DeepgramSession(socket, AsyncExitStack(), TranscriptionOptions(False, False))
    await socket.messages.put(
        Message(
            type="Results",
            is_final=True,
            speech_final=False,
            channel={"alternatives": [{"transcript": "Hello", "words": []}]},
        )
    )
    iterator = session.events()
    event = await anext(iterator)
    assert event.final
    assert event.segments == (Segment("Hello"),)
    assert event.audio_end == timedelta(seconds=1)
    await iterator.aclose()


@run_async
async def test_deepgram_silent_stop_waits_for_terminal_metadata() -> None:
    socket = DeepgramSocket()
    session = DeepgramSession(socket, AsyncExitStack(), TranscriptionOptions())
    await session.send(bytes(3200))

    async def receive() -> None:
        async for _event in session.events():
            pass

    receiver = asyncio.create_task(receive())
    async with asyncio.timeout(1):
        await session.flush()
    assert socket.commands == ["CloseStream"]
    receiver.cancel()
    with pytest.raises(asyncio.CancelledError):
        await receiver


class GeminiSocket:
    def __init__(self) -> None:
        self.calls = 0

    async def send_realtime_input(
        self, *, audio: types.Blob | None = None, audio_stream_end: bool | None = None
    ) -> None:
        pass

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        self.calls += 1
        yield types.LiveServerMessage(
            server_content=types.LiveServerContent(
                interim_input_transcription=types.Transcription(text="preview"),
                input_transcription=types.Transcription(text=f"Turn {self.calls}", finished=True),
                turn_complete=True,
            )
        )


@run_async
async def test_gemini_restarts_receive_after_each_completed_turn() -> None:
    socket = GeminiSocket()
    session = GeminiSession(socket, AsyncExitStack())
    iterator = session.events()
    events = [await anext(iterator) for _ in range(4)]
    assert [e.final for e in events] == [False, True, False, True]
    assert events[3].segments == (Segment("Turn 2"),)
    assert socket.calls == 2
    await iterator.aclose()
    await session.close()


class GeminiTranscribeSocket(GeminiSocket):
    """Mimics gemini-3.5-transcribe-live, which ends a flush with generation_complete only."""

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        yield types.LiveServerMessage(
            server_content=types.LiveServerContent(
                input_transcription=types.Transcription(text="Done", finished=True)
            )
        )
        yield types.LiveServerMessage(
            server_content=types.LiveServerContent(generation_complete=True)
        )
        await asyncio.Event().wait()


@run_async
async def test_gemini_flush_completes_on_generation_complete() -> None:
    session = GeminiSession(GeminiTranscribeSocket(), AsyncExitStack())
    texts: list[str] = []

    async def collect() -> None:
        async for event in session.events():
            texts.append(event.segments[0].text)

    receiver = asyncio.create_task(collect())
    await session.send(b"\0\0")
    async with asyncio.timeout(2):
        await session.flush()
    assert texts == ["Done"]
    receiver.cancel()
    await session.close()


class SilentGeminiSocket(GeminiSocket):
    """The server already finished its utterance, so the stream end gets no reply."""

    async def receive(self) -> AsyncIterator[types.LiveServerMessage]:
        await asyncio.Event().wait()
        yield types.LiveServerMessage()


@run_async
async def test_gemini_flush_returns_when_server_has_nothing_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("kikoe.providers.gemini.FLUSH_GRACE_SECONDS", 0.05)
    session = GeminiSession(SilentGeminiSocket(), AsyncExitStack())
    await session.send(b"\0\0")
    async with asyncio.timeout(2):
        await session.flush()
    await session.close()


@run_async
async def test_sdk_worker_cancellation_waits_for_remote_cleanup() -> None:
    started = asyncio.Event()
    cleaned = threading.Event()
    main_loop = asyncio.get_running_loop()

    async def operation() -> int:
        main_loop.call_soon_threadsafe(started.set)
        try:
            await asyncio.Event().wait()
            return 0
        finally:
            cleaned.set()

    task = asyncio.create_task(run_sdk_worker(operation))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
