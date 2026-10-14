"""Exercise Mistral's real SDK against offline HTTP responses."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx2 as httpx
import pytest

from helpers import file_caps, run_async
from kikoe.core.models import (
    ApiKind,
    AudioFormat,
    ModelSpec,
    Provider,
    Segment,
    TimestampGranularity,
)
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.core.upload import OGG_OPUS, UploadLimits, effective_upload_limits, upload_policy
from kikoe.core.validation import effective_base_url, validate_provider
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.factory import create_client
from kikoe.providers.mistral import MistralClient
from kikoe.storage.settings import Settings, SettingsStore

MODEL = ModelSpec("voxtral-mini-latest", "Voxtral Mini", file_caps())
FULL = ModelSpec(
    "voxtral-mini-latest",
    "Voxtral Mini",
    file_caps(timestamps=True, word_timestamps=True, diarization=True),
)


def body(text: str = "Hello", **extra: object) -> dict[str, object]:
    """The smallest response the SDK's models accept."""
    return {"model": "voxtral-mini-latest", "text": text, "usage": {}, "language": None, **extra}


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "upload.ogg"
    path.write_bytes(b"OggS fake audio")
    return path


def field(request: httpx.Request, name: str) -> str | None:
    marker = f'name="{name}"\r\n\r\n'.encode()
    content = request.content
    if marker not in content:
        return None
    start = content.index(marker) + len(marker)
    return content[start : content.index(b"\r\n", start)].decode()


def client_for(
    handler: httpx.MockTransport | None = None, url: str = "https://api.mistral.ai"
) -> MistralClient:
    return MistralClient(url, "secret", handler)


@run_async
@pytest.mark.parametrize("base_url", ["https://api.mistral.ai/", "https://proxy.test/mistral"])
async def test_sdk_uploads_audio_with_authentication_and_returns_plain_text(
    audio: Path, base_url: str
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body(" Hello "))

    result = await client_for(httpx.MockTransport(respond), base_url).transcribe_file(
        audio, MODEL, TranscriptionOptions()
    )

    assert result == (Segment("Hello"),)
    request = requests[0]
    assert str(request.url) == base_url.rstrip("/") + "/v1/audio/transcriptions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert field(request, "model") == MODEL.model_id
    assert b'filename="upload.ogg"' in request.content
    assert b"Content-Type: audio/ogg" in request.content


@run_async
async def test_empty_transcript_is_an_empty_success(audio: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body("  ")))

    assert await client_for(transport).transcribe_file(audio, MODEL, TranscriptionOptions()) == ()


def recorder(response: dict[str, object]) -> tuple[list[httpx.Request], httpx.MockTransport]:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=response)

    return requests, httpx.MockTransport(respond)


def fields_named(request: httpx.Request, name: str) -> list[str]:
    """Every value of a repeated multipart field, such as the granularity list."""
    marker = f'name="{name}"\r\n\r\n'.encode()
    parts = request.content.split(marker)[1:]
    return [part[: part.index(b"\r\n")].decode() for part in parts]


@run_async
async def test_segments_carry_times_and_the_language_is_not_sent_with_them(audio: Path) -> None:
    segment = {"text": " Hello ", "start": 1, "end": 2.5, "type": "transcription_segment"}
    requests, transport = recorder(body(segments=[segment]))

    result = await client_for(transport).transcribe_file(
        audio, FULL, TranscriptionOptions(diarization=False, language="zh-CN")
    )

    assert result == (Segment("Hello", timedelta(seconds=1), timedelta(seconds=2.5)),)
    assert fields_named(requests[0], "timestamp_granularities") == ["segment"]
    assert field(requests[0], "language") is None
    assert field(requests[0], "diarize") in (None, "false")


@run_async
async def test_plain_text_requests_send_the_primary_language_without_metadata(
    audio: Path,
) -> None:
    requests, transport = recorder(body())

    await client_for(transport).transcribe_file(
        audio, FULL, TranscriptionOptions(timestamps=False, diarization=False, language="zh-CN")
    )

    assert field(requests[0], "language") == "zh"
    assert fields_named(requests[0], "timestamp_granularities") == []
    assert field(requests[0], "diarize") in (None, "false")


@run_async
async def test_speakers_are_mapped_from_speaker_id_and_force_segment_times(audio: Path) -> None:
    segments = [
        {
            "text": "Hi.",
            "start": 0,
            "end": 1,
            "speaker_id": "speaker_1",
            "type": "transcription_segment",
        },
        {
            "text": "Hey.",
            "start": 1,
            "end": 2,
            "speaker_id": "speaker_2",
            "type": "transcription_segment",
        },
    ]
    requests, transport = recorder(body(segments=segments))

    result = await client_for(transport).transcribe_file(
        audio,
        FULL,
        TranscriptionOptions(language="en", timestamp_granularities=(TimestampGranularity.WORD,)),
    )

    assert [(s.text, s.speaker_id) for s in result] == [("Hi.", "speaker_1"), ("Hey.", "speaker_2")]
    assert field(requests[0], "diarize") == "true"
    assert fields_named(requests[0], "timestamp_granularities") == ["segment"]
    assert field(requests[0], "language") is None


@run_async
async def test_speakers_without_timestamps_discard_times(audio: Path) -> None:
    segment = {
        "text": "Hi.",
        "start": 0,
        "end": 1,
        "speaker_id": "speaker_1",
        "type": "transcription_segment",
    }
    _, transport = recorder(body(segments=[segment]))

    result = await client_for(transport).transcribe_file(
        audio, FULL, TranscriptionOptions(timestamps=False)
    )

    assert result == (Segment("Hi.", speaker_id="speaker_1"),)


@run_async
async def test_unrequested_speakers_are_not_reported(audio: Path) -> None:
    segment = {
        "text": "Hi.",
        "start": 0,
        "end": 1,
        "speaker_id": "speaker_1",
        "type": "transcription_segment",
    }
    requests, transport = recorder(body(segments=[segment]))

    result = await client_for(transport).transcribe_file(
        audio, FULL, TranscriptionOptions(diarization=False)
    )

    assert result[0].speaker_id is None
    assert field(requests[0], "diarize") in (None, "false")


@run_async
async def test_word_requests_group_words_into_sentences(audio: Path) -> None:
    words = [
        {"text": "Hello", "start": 0, "end": 0.5, "type": "transcription_segment"},
        {"text": "there.", "start": 0.5, "end": 1, "type": "transcription_segment"},
    ]
    requests, transport = recorder(body("Hello there.", segments=words))

    result = await client_for(transport).transcribe_file(
        audio,
        FULL,
        TranscriptionOptions(
            diarization=False, timestamp_granularities=(TimestampGranularity.WORD,)
        ),
    )

    assert fields_named(requests[0], "timestamp_granularities") == ["word"]
    assert len(result) == 1
    assert result[0].text == "Hello there."
    assert (result[0].start, result[0].end) == (timedelta(0), timedelta(seconds=1))


@run_async
async def test_segment_and_word_granularities_are_both_requested(audio: Path) -> None:
    segment = {"text": "Hi.", "start": 0, "end": 1, "type": "transcription_segment"}
    requests, transport = recorder(body(segments=[segment]))

    result = await client_for(transport).transcribe_file(
        audio,
        FULL,
        TranscriptionOptions(
            diarization=False,
            timestamp_granularities=(TimestampGranularity.SEGMENT, TimestampGranularity.WORD),
        ),
    )

    assert fields_named(requests[0], "timestamp_granularities") == ["segment", "word"]
    assert result == (Segment("Hi.", timedelta(0), timedelta(seconds=1)),)


@run_async
async def test_capabilities_limit_what_is_requested(audio: Path) -> None:
    requests, transport = recorder(body())

    await client_for(transport).transcribe_file(audio, MODEL, TranscriptionOptions(language="en"))

    assert field(requests[0], "language") == "en"
    assert fields_named(requests[0], "timestamp_granularities") == []
    assert field(requests[0], "diarize") in (None, "false")


@run_async
async def test_missing_segment_times_are_reported_when_requested(audio: Path) -> None:
    segment = {"text": "Hi.", "start": None, "end": None, "type": "transcription_segment"}
    _, transport = recorder(body(segments=[segment]))

    with pytest.raises(TranscriptionError, match="does not understand"):
        await client_for(transport).transcribe_file(
            audio, FULL, TranscriptionOptions(diarization=False)
        )


@run_async
@pytest.mark.parametrize("payload", [{}, {"text": 42}, "not an object"])
async def test_malformed_response_is_reported(audio: Path, payload: object) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))

    with pytest.raises(TranscriptionError, match="does not understand"):
        await client_for(transport).transcribe_file(audio, MODEL, TranscriptionOptions())


@run_async
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422, 429, 500])
async def test_sdk_errors_are_redacted_and_only_service_failures_retry(
    audio: Path, status: int
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"object": "error", "message": "bad secret"})

    with pytest.raises(TranscriptionError) as caught:
        await client_for(httpx.MockTransport(respond)).transcribe_file(
            audio, MODEL, TranscriptionOptions()
        )

    assert "secret" not in str(caught.value)
    assert caught.value.retryable is (status in (429, 500))
    assert len(requests) == 1


@run_async
async def test_service_message_is_shown_for_rejected_requests(audio: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(400, json={"message": "Unsupported audio format"})
    )

    with pytest.raises(TranscriptionError, match="Unsupported audio format"):
        await client_for(transport).transcribe_file(audio, MODEL, TranscriptionOptions())


@run_async
@pytest.mark.parametrize("is_timeout", [False, True])
async def test_network_failures_are_retryable(audio: Path, is_timeout: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if is_timeout:
            raise httpx.ReadTimeout("secret", request=request)
        raise httpx.ConnectError("secret", request=request)

    with pytest.raises(TranscriptionError) as caught:
        await client_for(httpx.MockTransport(respond)).transcribe_file(
            audio, MODEL, TranscriptionOptions()
        )

    assert caught.value.retryable
    assert "secret" not in str(caught.value)


@run_async
async def test_cancelling_upload_closes_transport(audio: Path) -> None:
    started = asyncio.Event()
    closed = asyncio.Event()

    class WaitingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError("Upload should be cancelled")

        async def aclose(self) -> None:
            closed.set()

    task = asyncio.create_task(
        MistralClient("https://api.mistral.ai", "secret", WaitingTransport()).transcribe_file(
            audio, MODEL, TranscriptionOptions()
        )
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


def test_mistral_configuration_roundtrips_and_uses_official_adapter(tmp_path: Path) -> None:
    assert validate_provider("Mistral", ApiKind.MISTRAL, "") is None
    provider = Provider(
        "mistral", "Mistral", ApiKind.MISTRAL, effective_base_url(ApiKind.MISTRAL, ""), (MODEL,)
    )
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((provider,)))

    assert provider.base_url == "https://api.mistral.ai"
    assert store.load_state().providers == (provider,)
    assert isinstance(create_client(provider, "secret"), MistralClient)
    policy = upload_policy(ApiKind.MISTRAL)
    assert (policy.bytes, policy.seconds) == (500_000_000, 3600)
    assert policy.format is AudioFormat.OGG
    assert effective_upload_limits(
        client_for().upload_limits(MODEL, TranscriptionOptions()), provider.upload, ApiKind.MISTRAL
    ) == UploadLimits(500_000_000, timedelta(hours=1), format=OGG_OPUS)


STREAMING = ModelSpec(
    "voxtral-mini-latest",
    "Voxtral Mini",
    file_caps(timestamps=True, word_timestamps=True, diarization=True, streaming=True),
)


def sse(*events: dict[str, object]) -> httpx.Response:
    """A server-sent event stream with the SDK's ``event``/``data`` framing."""
    content = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)


def delta(text: str) -> dict[str, object]:
    return {"type": "transcription.text.delta", "text": text}


def segment(text: str, start: float, end: float, speaker: str | None = None) -> dict[str, object]:
    return {
        "type": "transcription.segment",
        "text": text,
        "start": start,
        "end": end,
        "speaker_id": speaker,
    }


def done(text: str, segments: list[dict[str, object]] | None = None) -> dict[str, object]:
    final = [
        {**s, "type": "transcription_segment"} for s in segments or []
    ]  # The final response uses the non-streaming segment type.
    return {
        "type": "transcription.done",
        "model": "voxtral-mini-latest",
        "text": text,
        "usage": {},
        "language": None,
        "segments": final,
    }


@run_async
async def test_streaming_requests_the_event_stream_and_reports_growing_previews(
    audio: Path,
) -> None:
    requests: list[httpx.Request] = []
    events = [
        delta("Hello"),
        delta(" there."),
        segment("Hello there.", 0, 1),
        delta("More"),
        done("Hello there. More", [segment("Hello there.", 0, 1), segment("More", 1, 2)]),
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return sse(*events)

    snapshots: list[FileTranscriptSnapshot] = []
    result = await client_for(httpx.MockTransport(respond)).transcribe_file(
        audio,
        STREAMING,
        TranscriptionOptions(diarization=False),
        on_update=snapshots.append,
    )

    assert requests[0].headers["Accept"] == "text/event-stream"
    assert field(requests[0], "stream") in (None, "true")
    first, second = (
        Segment("Hello there.", timedelta(0), timedelta(seconds=1)),
        Segment("More", timedelta(seconds=1), timedelta(seconds=2)),
    )
    assert result == (first, second)
    assert [s.preview for s in snapshots[:2]] == [(Segment("Hello"),), (Segment("Hello there."),)]
    assert snapshots[2] == FileTranscriptSnapshot((first,), ())
    assert snapshots[3] == FileTranscriptSnapshot((first,), (Segment("More"),))
    assert snapshots[-1] == FileTranscriptSnapshot((first, second), ())


@run_async
async def test_streaming_keeps_speakers_from_the_final_event(audio: Path) -> None:
    final = done("Hi.", [segment("Hi.", 0, 1, "speaker_1")])
    transport = httpx.MockTransport(lambda request: sse(segment("Hi.", 0, 1, "speaker_1"), final))

    result = await client_for(transport).transcribe_file(audio, STREAMING, TranscriptionOptions())

    assert [(s.text, s.speaker_id) for s in result] == [("Hi.", "speaker_1")]


@run_async
async def test_streaming_word_requests_show_text_and_group_words_at_the_end(
    audio: Path,
) -> None:
    words = [segment("Hello", 0, 0.5), segment("there.", 0.5, 1)]
    transport = httpx.MockTransport(
        lambda request: sse(delta("Hello"), *words, done("Hello there.", words))
    )
    snapshots: list[FileTranscriptSnapshot] = []

    result = await client_for(transport).transcribe_file(
        audio,
        STREAMING,
        TranscriptionOptions(
            diarization=False, timestamp_granularities=(TimestampGranularity.WORD,)
        ),
        on_update=snapshots.append,
    )

    assert snapshots[0].preview == (Segment("Hello"),)
    assert [s.segments for s in snapshots[:-1]] == [()] * (len(snapshots) - 1)
    assert [s.text for s in result] == ["Hello there."]


@run_async
async def test_plain_streaming_uses_the_done_text(audio: Path) -> None:
    transport = httpx.MockTransport(lambda request: sse(delta("Hel"), done(" Hello ")))

    result = await client_for(transport).transcribe_file(
        audio, STREAMING, TranscriptionOptions(timestamps=False, diarization=False)
    )

    assert result == (Segment("Hello"),)


@run_async
async def test_stream_ending_without_done_is_a_retryable_failure(audio: Path) -> None:
    transport = httpx.MockTransport(lambda request: sse(delta("Hello")))

    with pytest.raises(TranscriptionError, match="before it was complete") as caught:
        await client_for(transport).transcribe_file(audio, STREAMING, TranscriptionOptions())

    assert caught.value.retryable


@run_async
async def test_unknown_stream_events_are_ignored(audio: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: sse(
            {"type": "transcription.language", "audio_language": "en"},
            done("Hi."),
        )
    )

    result = await client_for(transport).transcribe_file(
        audio, STREAMING, TranscriptionOptions(timestamps=False, diarization=False)
    )

    assert result == (Segment("Hi."),)


@run_async
async def test_streaming_http_errors_are_translated(audio: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"message": "bad secret"})
    )

    with pytest.raises(TranscriptionError, match="rejected the API key") as caught:
        await client_for(transport).transcribe_file(audio, STREAMING, TranscriptionOptions())

    assert "secret" not in str(caught.value)


@run_async
async def test_cancelling_a_stream_closes_the_response(audio: Path) -> None:
    started = asyncio.Event()
    closed = asyncio.Event()

    class Hanging(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"event: transcription.text.delta\n"
            yield b'data: {"type": "transcription.text.delta", "text": "Hi"}\n\n'
            started.set()
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            closed.set()

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Hanging()
        )
    )
    task = asyncio.create_task(
        client_for(transport).transcribe_file(audio, STREAMING, TranscriptionOptions())
    )
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
