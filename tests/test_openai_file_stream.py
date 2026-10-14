"""Exercise the official SDK's SSE decoder, including interrupted responses."""

import asyncio
import json
from datetime import timedelta
from pathlib import Path

import httpx2 as httpx
import pytest

from helpers import file_caps, form_field, run_async
from kikoe.core.models import Capabilities, ModelSpec, Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.openai import OpenAIClient
from test_openai_provider import Recorder

TEXT = ModelSpec("gpt-4o-transcribe", "GPT-4o", file_caps(streaming=True))
DIARIZE = ModelSpec(
    "gpt-4o-transcribe-diarize",
    "Diarize",
    file_caps(timestamps=True, diarization=True, streaming=True),
)
STREAMING_TEXT_MODELS = (
    "gpt-transcribe",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-4o-mini-transcribe-2025-12-15",
)


class EventBytes(httpx.AsyncByteStream):
    """Fragment events across UTF-8 and SSE boundaries, as a network may do."""

    def __init__(self, events: list[dict[str, object]], *, hang: bool = False) -> None:
        self.body = "".join(
            f"event: {event.get('type', 'error')}\n"
            f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            for event in events
        ).encode()
        self.closed = False
        self.hang = hang
        self.waiting = asyncio.Event()

    async def __aiter__(self):
        for index in range(0, len(self.body), 7):
            yield self.body[index : index + 7]
        if self.hang:
            self.waiting.set()
            await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


def delta(text: str, id: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {"type": "transcript.text.delta", "delta": text}
    if id is not None:
        result["segment_id"] = id
    return result


def done(text: str) -> dict[str, object]:
    return {"type": "transcript.text.done", "text": text}


def segment(id: str, text: str, start: float, speaker: str = "A") -> dict[str, object]:
    return {
        "type": "transcript.text.segment",
        "id": id,
        "text": text,
        "start": start,
        "end": start + 1,
        "speaker": speaker,
    }


def client(events: EventBytes) -> tuple[OpenAIClient, Recorder]:
    recorder = Recorder(
        httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=events)
    )
    return OpenAIClient(
        "https://api.openai.com/v1",
        "secret",
        transport=httpx.MockTransport(recorder),
    ), recorder


@run_async
@pytest.mark.parametrize("model", STREAMING_TEXT_MODELS)
async def test_stream_preserves_spaces_and_uses_corrected_done_text(
    audio: Path, model: str
) -> None:
    events = EventBytes([delta("你好 "), delta("world"), done("你好 world!")])
    sdk, requests = client(events)
    updates: list[FileTranscriptSnapshot] = []
    result = await sdk.transcribe_file(
        audio,
        ModelSpec(model, model, file_caps(streaming=True)),
        TranscriptionOptions(language="zh"),
        on_update=updates.append,
    )
    assert updates == [
        FileTranscriptSnapshot(preview=(Segment("你好 "),)),
        FileTranscriptSnapshot(preview=(Segment("你好 world"),)),
        FileTranscriptSnapshot((Segment("你好 world!"),)),
    ]
    assert result == (Segment("你好 world!"),)
    assert events.closed
    request = requests.requests[0]
    assert form_field(request, "stream") == "true"
    assert form_field(request, "response_format") == "json"
    assert form_field(request, "language") == "zh"
    assert form_field(request, "timestamp_granularities[]") is None


@pytest.mark.parametrize("timestamps,diarization", [(True, True), (True, False), (False, True)])
@run_async
async def test_diarized_segments_replace_previews_without_duplicating_done(
    audio: Path,
    timestamps: bool,
    diarization: bool,
) -> None:
    events = EventBytes(
        [
            delta("Hel", "a"),
            delta("Other", "b"),
            segment("a", "Hello", 0),
            segment("a", "Hello!", 0),
            delta("ignored", "a"),
            segment("b", "Other.", 2, "B"),
            done("Hello! Other."),
        ]
    )
    sdk, requests = client(events)
    updates: list[FileTranscriptSnapshot] = []
    result = await sdk.transcribe_file(
        audio,
        DIARIZE,
        TranscriptionOptions(timestamps, diarization),
        on_update=updates.append,
    )
    assert result == (
        Segment(
            "Hello!",
            timedelta() if timestamps else None,
            timedelta(seconds=1) if timestamps else None,
            "A" if diarization else None,
        ),
        Segment(
            "Other.",
            timedelta(seconds=2) if timestamps else None,
            timedelta(seconds=3) if timestamps else None,
            "B" if diarization else None,
        ),
    )
    assert updates[2].preview == (Segment("Other"),)
    assert updates[-1] == FileTranscriptSnapshot(result)
    assert form_field(requests.requests[0], "response_format") == "diarized_json"
    assert form_field(requests.requests[0], "chunking_strategy") == "auto"
    assert events.closed


@run_async
async def test_diarize_with_no_metadata_streams_plain_json(audio: Path) -> None:
    sdk, requests = client(EventBytes([delta("Hello"), done("Hello")]))
    assert await sdk.transcribe_file(audio, DIARIZE, TranscriptionOptions(False, False)) == (
        Segment("Hello"),
    )
    assert form_field(requests.requests[0], "response_format") == "json"
    assert form_field(requests.requests[0], "chunking_strategy") == "auto"


@pytest.mark.parametrize("model", [TEXT, DIARIZE])
@run_async
async def test_empty_completed_stream_is_success(audio: Path, model: ModelSpec) -> None:
    sdk, _ = client(EventBytes([done("  ")]))
    assert await sdk.transcribe_file(audio, model, TranscriptionOptions()) == ()


@pytest.mark.parametrize("events", [[], [delta("partial")], [segment("a", "partial", 0)]])
@run_async
async def test_eof_without_done_is_retryable_and_closes_response(
    audio: Path,
    events: list[dict[str, object]],
) -> None:
    body = EventBytes(events)
    sdk, _ = client(body)
    with pytest.raises(TranscriptionError, match="before it was complete") as failure:
        await sdk.transcribe_file(audio, DIARIZE, TranscriptionOptions())
    assert failure.value.retryable
    assert body.closed


@run_async
async def test_diarized_done_cannot_silently_lose_requested_metadata(audio: Path) -> None:
    sdk, _ = client(EventBytes([done("Missing segments")]))
    with pytest.raises(TranscriptionError, match="does not understand"):
        await sdk.transcribe_file(audio, DIARIZE, TranscriptionOptions())


@run_async
async def test_stream_error_is_translated_and_key_is_redacted(audio: Path) -> None:
    body = EventBytes([delta("partial"), {"error": {"message": "secret rejected"}}])
    sdk, _ = client(body)
    with pytest.raises(TranscriptionError, match="could not finish transcription") as failure:
        await sdk.transcribe_file(audio, TEXT, TranscriptionOptions())
    assert "secret" not in str(failure.value)
    assert body.closed


@run_async
async def test_cancelling_stream_closes_response_and_does_not_return_preview(audio: Path) -> None:
    body = EventBytes([delta("partial")], hang=True)
    sdk, _ = client(body)
    task = asyncio.create_task(sdk.transcribe_file(audio, TEXT, TranscriptionOptions()))
    await body.waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert body.closed


@run_async
async def test_done_closes_stream_without_waiting_for_the_server_to_disconnect(audio: Path) -> None:
    body = EventBytes([done("Complete")], hang=True)
    sdk, _ = client(body)
    async with asyncio.timeout(1):
        assert await sdk.transcribe_file(audio, TEXT, TranscriptionOptions()) == (
            Segment("Complete"),
        )
    assert body.closed


@run_async
async def test_diarized_segments_keep_audio_order_even_when_timestamps_are_hidden(
    audio: Path,
) -> None:
    sdk, _ = client(
        EventBytes(
            [
                segment("later", "Later", 5),
                segment("first", "First", 0),
                done("First Later"),
            ]
        )
    )
    assert await sdk.transcribe_file(audio, DIARIZE, TranscriptionOptions(False, True)) == (
        Segment("First", speaker_id="A"),
        Segment("Later", speaker_id="A"),
    )


@pytest.mark.parametrize("model", ["whisper-1", "gpt-4o-transcribe"])
@run_async
async def test_models_without_streaming_keep_nonstreaming_requests(audio: Path, model: str) -> None:
    handler = Recorder(httpx.Response(200, json={"text": "Text"}))
    sdk = OpenAIClient(
        "https://api.openai.com/v1", "secret", transport=httpx.MockTransport(handler)
    )
    assert await sdk.transcribe_file(
        audio, ModelSpec(model, model, Capabilities()), TranscriptionOptions()
    ) == (Segment("Text"),)
    assert form_field(handler.requests[0], "stream") is None


@run_async
async def test_compatible_providers_stream_when_the_model_allows_it(audio: Path) -> None:
    events = EventBytes([delta("Hi"), done("Hi")])
    recorder = Recorder(
        httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=events)
    )
    sdk = OpenAIClient("http://localhost:8000/v1", "", transport=httpx.MockTransport(recorder))
    assert await sdk.transcribe_file(audio, TEXT, TranscriptionOptions()) == (Segment("Hi"),)
    assert form_field(recorder.requests[0], "stream") == "true"
