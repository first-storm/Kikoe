"""Run the real Mistral SDK realtime client against a WebSocket server on the loopback."""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from kikoe.core.models import ModelSpec, Segment
from kikoe.core.transcription import LiveTranscript, TranscriptEvent
from kikoe.providers import mistral_realtime
from kikoe.providers.base import RealtimeSession, TranscriptionError, TranscriptionOptions
from kikoe.providers.mistral import MistralClient
from tests.helpers import LIVE_ONLY, run_async

MODEL = ModelSpec("voxtral-mini-transcribe-realtime-2602", "Voxtral Realtime", LIVE_ONLY)
CREATED = {
    "type": "session.created",
    "session": {
        "request_id": "r1",
        "model": MODEL.model_id,
        "audio_format": {"encoding": "pcm_s16le", "sample_rate": 16000},
    },
}
PCM = bytes([1, 0]) * 1600

type Handler = Callable[[ServerConnection, list[dict[str, object]]], Awaitable[None]]


class Server:
    """Records what the client sent so tests can check the wire protocol."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.received: list[dict[str, object]] = []
        self.requests: list[Request] = []
        self.url = ""

    async def serve(self, connection: ServerConnection) -> None:
        await connection.send(json.dumps(CREATED))
        await self.handler(connection, self.received)


@asynccontextmanager
async def running(
    handler: Handler,
    reject: int | None = None,
) -> AsyncGenerator[Server]:
    server = Server(handler)

    def process_request(connection: ServerConnection, request: Request) -> Response | None:
        server.requests.append(request)
        if reject is None:
            return None
        return Response(reject, "Rejected", Headers(), b"")

    async with serve(server.serve, "127.0.0.1", 0, process_request=process_request) as ws:
        port = int(next(iter(ws.sockets)).getsockname()[1])
        server.url = f"http://127.0.0.1:{port}"
        yield server


async def record(connection: ServerConnection, received: list[dict[str, object]]) -> None:
    """Keep every client message and answer a flush with two text deltas."""
    async for raw in connection:
        message = json.loads(raw)
        received.append(message)
        if message["type"] == "input_audio.flush":
            for text in ("Hel", "lo"):
                await connection.send(
                    json.dumps({"type": "transcription.text.delta", "text": text})
                )


@pytest.fixture(autouse=True)
def fast_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mistral_realtime, "QUIET_SECONDS", 0.05)
    monkeypatch.setattr(mistral_realtime, "FLUSH_LIMIT_SECONDS", 2.0)


async def collect(session: RealtimeSession, count: int) -> list[TranscriptEvent]:
    events: list[TranscriptEvent] = []
    async with asyncio.timeout(5):
        async for event in session.events():
            events.append(event)
            if len(events) == count:
                break
    return events


@run_async
async def test_session_requests_the_model_with_authentication_and_pcm_format() -> None:
    async with running(record) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        await session.send(PCM)
        await asyncio.sleep(0.1)
        await session.close()

    request = server.requests[0]
    assert request.path.startswith("/v1/audio/transcriptions/realtime?")
    assert f"model={MODEL.model_id}" in request.path
    assert request.headers["Authorization"] == "Bearer secret"
    update = next(m for m in server.received if m["type"] == "session.update")
    assert update["session"] == {"audio_format": {"encoding": "pcm_s16le", "sample_rate": 16000}}
    append = next(m for m in server.received if m["type"] == "input_audio.append")
    assert base64.b64decode(str(append["audio"])) == PCM


@run_async
async def test_flush_finalizes_the_turn_after_streaming_previews() -> None:
    async with running(record) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        await session.send(PCM)
        await session.flush()
        events = await collect(session, 3)
        await session.close()

    assert events == [
        TranscriptEvent("0", (Segment("Hel"),), False),
        TranscriptEvent("0", (Segment("Hello"),), False),
        TranscriptEvent("0", (Segment("Hello"),), True),
    ]
    assert [m["type"] for m in server.received].count("input_audio.flush") == 1


@run_async
async def test_each_flush_starts_a_new_turn_that_live_text_keeps_apart() -> None:
    async with running(record) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        for _ in range(2):
            await session.send(PCM)
            await session.flush()
        events = await collect(session, 6)
        await session.close()

    live = LiveTranscript()
    for event in events:
        live.apply(event)
    assert [e.id for e in events if e.final] == ["0", "1"]
    assert [s.text for s in live.segments] == ["Hello", "Hello"]
    assert live.preview == ()


@run_async
async def test_flush_without_new_audio_sends_nothing() -> None:
    async with running(record) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        await session.flush()
        await asyncio.sleep(0.1)
        await session.close()

    assert "input_audio.flush" not in [m["type"] for m in server.received]


@run_async
async def test_silent_audio_finalizes_without_an_empty_event() -> None:
    async def silent(connection: ServerConnection, received: list[dict[str, object]]) -> None:
        async for raw in connection:
            received.append(json.loads(raw))

    async with running(silent) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        await session.send(PCM)
        await session.flush()
        await session.close()
        remaining = [event async for event in session.events()]

    assert remaining == []


@run_async
async def test_error_events_are_reported_without_the_key() -> None:
    async def fail(connection: ServerConnection, received: list[dict[str, object]]) -> None:
        await connection.send(
            json.dumps({"type": "error", "error": {"message": "bad secret", "code": 400}})
        )
        await connection.wait_closed()

    async with running(fail) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        with pytest.raises(TranscriptionError, match="rejected realtime") as caught:
            await collect(session, 1)
        await session.close()

    assert "secret" not in str(caught.value)


@run_async
async def test_connection_closed_by_the_server_ends_the_event_stream() -> None:
    async def hang_up(connection: ServerConnection, received: list[dict[str, object]]) -> None:
        await connection.recv()  # the session update, so the client is fully connected
        await connection.close()

    async with running(hang_up) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        assert await collect(session, 1) == []
        await session.close()


@run_async
async def test_rejected_handshake_reports_the_api_key() -> None:
    async with running(record, reject=401) as server:
        with pytest.raises(TranscriptionError, match="rejected the API key"):
            await MistralClient(server.url, "secret").open_realtime(MODEL, TranscriptionOptions())


@run_async
async def test_unreachable_server_is_retryable() -> None:
    with pytest.raises(TranscriptionError, match="Could not reach") as caught:
        await MistralClient("http://127.0.0.1:1", "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )

    assert caught.value.retryable


@run_async
async def test_closing_while_waiting_for_events_stops_quickly() -> None:
    async with running(record) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )
        waiting = asyncio.create_task(collect(session, 1))
        await asyncio.sleep(0.05)
        async with asyncio.timeout(3):
            await session.close()
        assert await waiting == []


@run_async
async def test_session_requests_the_configured_delay() -> None:
    async with running(record) as server:
        session = await MistralClient(server.url, "secret").open_realtime(
            MODEL, TranscriptionOptions(realtime_delay_ms=480)
        )
        await session.send(PCM)
        await asyncio.sleep(0.1)
        await session.close()

    update = next(m for m in server.received if m["type"] == "session.update")
    assert update["session"] == {
        "audio_format": {"encoding": "pcm_s16le", "sample_rate": 16000},
        "target_streaming_delay_ms": 480,
    }
