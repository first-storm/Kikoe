from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from helpers import run_async
from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Provider, Segment
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.factory import create_client
from kikoe.providers.openai import MAX_UPLOAD_BYTES, OpenAIClient

BASE_URL = "https://api.groq.com/openai/v1/"
WHISPER = ModelSpec("whisper-large-v3", "Whisper", Capabilities(timestamps=True))
TEXT_ONLY = ModelSpec("gpt-4o-transcribe", "GPT-4o", Capabilities())

# Trimmed from a real Groq verbose_json response.
VERBOSE_BODY = {
    "task": "transcribe",
    "language": "English",
    "duration": 5.2,
    "text": " Hello there. How are you?",
    "segments": [
        {"id": 0, "start": 0.0, "end": 1.84, "text": " Hello there."},
        {"id": 1, "start": 1.84, "end": 5.2, "text": " How are you?"},
        {"id": 2, "start": 5.2, "end": 5.2, "text": " "},
    ],
}


class Recorder:
    """Answers every request with a fixed response and remembers the requests."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        return self.response


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    path = tmp_path / "upload.ogg"
    path.write_bytes(b"OggS fake audio")
    return path


def client(handler: Recorder, api_key: str = "secret") -> OpenAIClient:
    return OpenAIClient(BASE_URL, api_key, transport=httpx.MockTransport(handler))


def form_field(request: httpx.Request, name: str) -> str | None:
    """Read a plain field from the multipart body (good enough for these small requests)."""
    marker = f'name="{name}"\r\n\r\n'.encode()
    body = request.content
    if marker not in body:
        return None
    start = body.index(marker) + len(marker)
    return body[start : body.index(b"\r\n", start)].decode()


@run_async
async def test_timestamped_model_gets_segments(audio: Path) -> None:
    handler = Recorder(httpx.Response(200, json=VERBOSE_BODY))

    segments = await client(handler).transcribe_file(audio, WHISPER, TranscriptionOptions())

    assert segments == (
        Segment("Hello there.", timedelta(0), timedelta(seconds=1.84)),
        Segment("How are you?", timedelta(seconds=1.84), timedelta(seconds=5.2)),
    )
    request = handler.requests[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert form_field(request, "model") == "whisper-large-v3"
    assert form_field(request, "response_format") == "verbose_json"
    assert form_field(request, "timestamp_granularities[]") == "segment"
    assert b'filename="upload.ogg"' in request.content
    assert b"Content-Type: audio/ogg" in request.content


@run_async
async def test_text_only_model_gets_one_untimed_segment(audio: Path) -> None:
    handler = Recorder(httpx.Response(200, json={"text": "Just text."}))

    segments = await client(handler).transcribe_file(audio, TEXT_ONLY, TranscriptionOptions())

    assert segments == (Segment("Just text."),)
    assert form_field(handler.requests[0], "response_format") == "json"
    assert form_field(handler.requests[0], "timestamp_granularities[]") is None


@run_async
async def test_timestamps_turned_off_requests_plain_json(audio: Path) -> None:
    handler = Recorder(httpx.Response(200, json={"text": "Hi"}))

    await client(handler).transcribe_file(audio, WHISPER, TranscriptionOptions(timestamps=False))

    assert form_field(handler.requests[0], "response_format") == "json"


@run_async
async def test_empty_transcript_gives_no_segments(audio: Path) -> None:
    handler = Recorder(httpx.Response(200, json={"text": "  ", "segments": []}))

    assert await client(handler).transcribe_file(audio, WHISPER, TranscriptionOptions()) == ()


@run_async
async def test_language_is_sent_when_chosen(audio: Path) -> None:
    handler = Recorder(httpx.Response(200, json={"text": "Hallo"}))

    await client(handler).transcribe_file(audio, TEXT_ONLY, TranscriptionOptions(language="de"))

    assert form_field(handler.requests[0], "language") == "de"


@run_async
async def test_empty_key_sends_no_authorization(audio: Path) -> None:
    handler = Recorder(httpx.Response(200, json={"text": "Hi"}))

    await client(handler, api_key="").transcribe_file(audio, TEXT_ONLY, TranscriptionOptions())

    assert "Authorization" not in handler.requests[0].headers


@run_async
async def test_rejected_key_points_to_preferences(audio: Path) -> None:
    body = {"error": {"message": "Invalid API Key", "type": "invalid_request_error"}}
    handler = Recorder(httpx.Response(401, json=body))

    with pytest.raises(TranscriptionError, match="rejected the API key"):
        await client(handler).transcribe_file(audio, WHISPER, TranscriptionOptions())


@run_async
async def test_api_error_message_is_passed_on(audio: Path) -> None:
    body = {"error": {"message": "The model `nope` does not exist", "code": "model_not_found"}}
    handler = Recorder(httpx.Response(404, json=body))

    with pytest.raises(TranscriptionError, match=r"api\.groq\.com reported an error: The model"):
        await client(handler).transcribe_file(audio, WHISPER, TranscriptionOptions())


@run_async
async def test_error_without_body_reports_the_status(audio: Path) -> None:
    handler = Recorder(httpx.Response(502, text="Bad Gateway"))

    with pytest.raises(TranscriptionError, match="HTTP status 502"):
        await client(handler).transcribe_file(audio, WHISPER, TranscriptionOptions())


@run_async
async def test_unexpected_body_is_reported(audio: Path) -> None:
    handler = Recorder(httpx.Response(200, json={"segments": [{"text": "no times"}]}))

    with pytest.raises(TranscriptionError, match="does not understand"):
        await client(handler).transcribe_file(audio, WHISPER, TranscriptionOptions())


@run_async
async def test_connection_failure_names_the_host(audio: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    failing = OpenAIClient(BASE_URL, "k", transport=httpx.MockTransport(refuse))

    with pytest.raises(TranscriptionError, match=r"Could not reach api\.groq\.com"):
        await failing.transcribe_file(audio, WHISPER, TranscriptionOptions())


@run_async
async def test_oversize_file_is_refused_before_upload(tmp_path: Path) -> None:
    big = tmp_path / "big.ogg"
    with big.open("wb") as file:
        file.truncate(MAX_UPLOAD_BYTES + 1)
    handler = Recorder(httpx.Response(200, json={"text": "never"}))

    with pytest.raises(TranscriptionError, match="too large"):
        await client(handler).transcribe_file(big, WHISPER, TranscriptionOptions())
    assert handler.requests == []


def test_factory_supports_openai_style_apis() -> None:
    provider = Provider("p", "Groq", ApiKind.OPENAI_COMPATIBLE, BASE_URL)

    assert isinstance(create_client(provider, "k"), OpenAIClient)


def test_factory_refuses_deepgram_for_now() -> None:
    provider = Provider("p", "Deepgram", ApiKind.DEEPGRAM, "https://api.deepgram.com/v1")

    with pytest.raises(TranscriptionError, match="Deepgram is not supported"):
        create_client(provider, "k")
