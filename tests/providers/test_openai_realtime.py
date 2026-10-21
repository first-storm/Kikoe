"""Run the real OpenAI SDK realtime handshake against a WebSocket server on the loopback."""

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from kikoe.core.models import ModelSpec
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.openai import OpenAIClient
from tests.helpers import LIVE_ONLY, run_async

MODEL = ModelSpec("whisper-large-v3-turbo", "Whisper", LIVE_ONLY)
# Trimmed from Groq's answer to a WebSocket upgrade on /openai/v1/realtime.
UNKNOWN_URL = (
    b'{"error":{"message":"Unknown request URL: GET /openai/v1/realtime.",'
    b'"type":"invalid_request_error","code":"unknown_url"}}'
)


@asynccontextmanager
async def rejecting(status: int, body: bytes = b"") -> AsyncGenerator[str]:
    """Refuse every upgrade with ``status``; yields the base URL to give the client."""

    def process_request(connection: ServerConnection, request: Request) -> Response:
        return Response(status, "Rejected", Headers({"Content-Type": "application/json"}), body)

    async def never(connection: ServerConnection) -> None:
        raise AssertionError("The handshake should have been refused")

    async with serve(never, "127.0.0.1", 0, process_request=process_request) as server:
        port = int(next(iter(server.sockets)).getsockname()[1])
        yield f"http://127.0.0.1:{port}/openai/v1"


@run_async
async def test_service_without_a_realtime_endpoint_says_to_turn_live_transcription_off() -> None:
    async with rejecting(404, UNKNOWN_URL) as url:
        with pytest.raises(TranscriptionError) as caught:
            await OpenAIClient(url, "secret").open_realtime(MODEL, TranscriptionOptions())

    message = str(caught.value)
    assert "127.0.0.1 does not support transcription while recording" in message
    assert "Transcribe While Recording" in message
    assert "model name" not in message
    assert not caught.value.retryable


@run_async
async def test_rejected_handshake_reports_the_api_key() -> None:
    async with rejecting(401) as url:
        with pytest.raises(TranscriptionError, match="rejected the API key"):
            await OpenAIClient(url, "secret").open_realtime(MODEL, TranscriptionOptions())


@pytest.mark.parametrize("acknowledged", [False, True])
@run_async
async def test_realtime_session_requires_the_server_to_acknowledge_configuration(
    acknowledged: bool,
) -> None:
    requests: list[str] = []

    async def handshake(connection: ServerConnection) -> None:
        message = json.loads(await connection.recv())
        requests.append(message["type"])
        if acknowledged:
            await connection.send(json.dumps({"type": "session.updated"}))
            await connection.wait_closed()
        else:
            await connection.close(code=1000)

    async with serve(handshake, "127.0.0.1", 0) as server:
        port = int(next(iter(server.sockets)).getsockname()[1])
        client = OpenAIClient(f"http://127.0.0.1:{port}/v1", "fake-key")
        if acknowledged:
            session = await client.open_realtime(MODEL, TranscriptionOptions())
            await session.close()
        else:
            # Close an incorrectly returned session too, so the old implementation leaks nothing.
            with pytest.raises(TranscriptionError) as caught:
                session = await client.open_realtime(MODEL, TranscriptionOptions())
                await session.close()
            assert caught.value.retryable
    assert requests == ["session.update"]
