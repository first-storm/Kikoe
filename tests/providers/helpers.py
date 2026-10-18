"""Fake HTTP responses shared by provider tests."""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx2 as httpx


def completion(text: str | None) -> dict[str, object]:
    """A chat response with optional content, including the SDK's required metadata."""
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "mimo-v2.5-asr",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
    }


def chunk(text: str | None = None, reason: str | None = None) -> dict[str, object]:
    """A chat stream delta that can signal a final stop or a rejected completion."""
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "mimo-v2.5-asr",
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": reason}],
    }


class EventBytes(httpx.AsyncByteStream):
    """Split UTF-8 and SSE boundaries and expose response lifecycle to tests."""

    def __init__(
        self, events: list[dict[str, object]], *, done: bool = True, hang: bool = False
    ) -> None:
        self.body = "".join(
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n" for event in events
        ).encode()
        if done:
            self.body += b"data: [DONE]\n\n"
        self.closed = False
        self.hang = hang
        self.waiting = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for offset in range(0, len(self.body), 7):
            yield self.body[offset : offset + 7]
        if self.hang:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class Recorder:
    """Answers every request with a fixed response and remembers the requests."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return self.response
