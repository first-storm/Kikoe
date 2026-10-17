"""Exercise MiMo chat SSE decoding and reject incomplete or filtered transcription."""

import asyncio
import json
from pathlib import Path

import httpx2 as httpx
import pytest

from helpers import file_caps, run_async
from kikoe.core.models import ModelSpec, Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.mimo import MiMoClient
from test_mimo_provider import completion

MODEL = ModelSpec("mimo-v2.5-asr", "MiMo", file_caps(streaming=True))


def chunk(text: str | None = None, reason: str | None = None) -> dict[str, object]:
    return {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": MODEL.model_id,
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

    async def __aiter__(self):
        for offset in range(0, len(self.body), 7):
            yield self.body[offset : offset + 7]
        if self.hang:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


def client(events: EventBytes, requests: list[httpx.Request]) -> MiMoClient:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=events)

    return MiMoClient("https://example.test/v1", "secret", httpx.MockTransport(respond))


@run_async
async def test_stream_preserves_spaces_and_commits_only_after_stop(audio: Path) -> None:
    metadata: dict[str, object] = {**chunk(), "choices": []}
    events = EventBytes([metadata, chunk(), chunk(" 你好 "), chunk("world"), chunk("! ", "stop")])
    requests: list[httpx.Request] = []
    updates: list[FileTranscriptSnapshot] = []
    result = await client(events, requests).transcribe_file(
        audio, MODEL, TranscriptionOptions(language="zh-CN"), on_update=updates.append
    )
    assert result == (Segment("你好 world!"),)
    assert updates == [
        FileTranscriptSnapshot(preview=(Segment(" 你好 "),)),
        FileTranscriptSnapshot(preview=(Segment(" 你好 world"),)),
        FileTranscriptSnapshot((Segment("你好 world!"),)),
    ]
    assert events.closed
    body = json.loads(requests[0].content)
    assert body["stream"] is True
    assert body["asr_options"] == {"language": "zh"}
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer secret"
    assert body["messages"][0]["content"][0]["input_audio"]["data"].startswith(
        "data:audio/wav;base64,"
    )


@run_async
async def test_stream_works_without_a_preview_callback(audio: Path) -> None:
    events = EventBytes([chunk("hello", "stop")])
    assert await client(events, []).transcribe_file(audio, MODEL, TranscriptionOptions()) == (
        Segment("hello"),
    )
    assert events.closed


@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls", "unknown"])
@pytest.mark.parametrize("streaming", [False, True])
@run_async
async def test_unsuccessful_finish_never_commits_partial_text(
    audio: Path, reason: str, streaming: bool
) -> None:
    events = EventBytes([chunk("partial"), chunk(reason=reason)])
    updates: list[FileTranscriptSnapshot] = []
    body = completion("partial")
    body["choices"] = [
        {
            "index": 0,
            "finish_reason": reason,
            "message": {"role": "assistant", "content": "partial"},
        }
    ]
    sdk = (
        client(events, [])
        if streaming
        else MiMoClient(
            "https://example.test/v1",
            "secret",
            httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
        )
    )
    model = ModelSpec(MODEL.model_id, "MiMo", file_caps(streaming=streaming))
    with pytest.raises(TranscriptionError) as error:
        await sdk.transcribe_file(audio, model, TranscriptionOptions(), on_update=updates.append)
    assert not error.value.retryable
    assert all(not update.segments for update in updates)
    if reason == "length":
        assert "chunk length or size" in str(error.value)
    if reason == "content_filter":
        assert "content filter" in str(error.value)
    if streaming:
        assert events.closed


@pytest.mark.parametrize("done", [False, True])
@run_async
async def test_eof_without_finish_is_retryable_even_after_text(audio: Path, done: bool) -> None:
    events = EventBytes([chunk("partial")], done=done)
    with pytest.raises(TranscriptionError, match="before it was complete") as error:
        await client(events, []).transcribe_file(audio, MODEL, TranscriptionOptions())
    assert error.value.retryable
    assert events.closed


@run_async
async def test_empty_successful_stream_is_rejected(audio: Path) -> None:
    events = EventBytes([chunk("  ", "stop")])
    with pytest.raises(TranscriptionError, match="does not understand"):
        await client(events, []).transcribe_file(audio, MODEL, TranscriptionOptions())
    assert events.closed


@run_async
async def test_cancelled_stream_closes_without_committing(audio: Path) -> None:
    events = EventBytes([chunk("partial")], done=False, hang=True)
    updates: list[FileTranscriptSnapshot] = []
    task = asyncio.create_task(
        client(events, []).transcribe_file(
            audio, MODEL, TranscriptionOptions(), on_update=updates.append
        )
    )
    await asyncio.wait_for(events.waiting.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events.closed
    assert updates == [FileTranscriptSnapshot(preview=(Segment("partial"),))]


@run_async
async def test_nonstreaming_missing_finish_reason_is_rejected(audio: Path) -> None:
    body = completion("partial")
    body["choices"] = [{"index": 0, "message": {"role": "assistant", "content": "partial"}}]
    sdk = MiMoClient(
        "https://example.test/v1",
        "secret",
        httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )
    with pytest.raises(TranscriptionError, match="does not understand"):
        await sdk.transcribe_file(
            audio, ModelSpec(MODEL.model_id, "MiMo", file_caps()), TranscriptionOptions()
        )


@run_async
async def test_failed_connection_during_stream_closes_and_keeps_only_previews(audio: Path) -> None:
    class BrokenStream(EventBytes):
        async def __aiter__(self):
            async for part in super().__aiter__():
                yield part
            raise httpx.ReadError("connection lost")

    events = BrokenStream([chunk("partial")], done=False)
    updates: list[FileTranscriptSnapshot] = []
    with pytest.raises(TranscriptionError, match="Could not reach") as error:
        await client(events, []).transcribe_file(
            audio, MODEL, TranscriptionOptions(), on_update=updates.append
        )
    assert events.closed
    assert error.value.retryable
    assert updates == [FileTranscriptSnapshot(preview=(Segment("partial"),))]


@run_async
async def test_streaming_http_failure_redacts_api_key(audio: Path) -> None:
    sdk = MiMoClient(
        "https://example.test/v1",
        "secret",
        httpx.MockTransport(
            lambda request: httpx.Response(400, json={"error": {"message": "bad secret"}})
        ),
    )
    with pytest.raises(TranscriptionError) as error:
        await sdk.transcribe_file(audio, MODEL, TranscriptionOptions())
    assert "secret" not in str(error.value)
    assert "[redacted]" in str(error.value)
