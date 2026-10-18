"""Exercise Groq's real SDK against offline HTTP responses."""

import asyncio
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from kikoe.core.models import (
    ApiKind,
    AudioFormat,
    ModelSpec,
    Provider,
    Segment,
)
from kikoe.core.provider_registry import descriptor
from kikoe.core.upload import OGG_OPUS, UploadLimits, effective_upload_limits
from kikoe.core.validation import effective_base_url, validate_provider
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.groq import GroqClient
from kikoe.storage.model_catalog import load_catalog
from kikoe.storage.settings import Settings, SettingsStore
from tests.helpers import form_field, run_async

MODEL = ModelSpec(
    "whisper-large-v3-turbo",
    "Whisper",
    load_catalog().preset(ApiKind.GROQ, "whisper-large-v3-turbo").defaults(),
)


@run_async
@pytest.mark.parametrize("base_url", ["https://api.groq.com/", "https://proxy.test/groq"])
async def test_sdk_uploads_segments_with_authentication_and_primary_language(
    audio: Path, base_url: str
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200, json={"text": "Hello", "segments": [{"text": " Hello ", "start": 1, "end": 2}]}
        )

    client = GroqClient(base_url, "secret", httpx.MockTransport(respond))
    result = await client.transcribe_file(audio, MODEL, TranscriptionOptions(language="zh-CN"))
    assert result == (Segment("Hello", timedelta(seconds=1), timedelta(seconds=2)),)
    request = requests[0]
    assert str(request.url) == base_url.rstrip("/") + "/openai/v1/audio/transcriptions"
    assert request.headers["Authorization"] == "Bearer secret"
    assert form_field(request, "model") == MODEL.model_id
    assert form_field(request, "language") == "zh"
    assert form_field(request, "response_format") == "verbose_json"
    assert form_field(request, "timestamp_granularities[]") == "segment"
    assert form_field(request, "stream") is None
    assert form_field(request, "diarize") is None
    assert b'filename="upload.ogg"' in request.content
    assert b"Content-Type: audio/ogg" in request.content


@run_async
@pytest.mark.parametrize("text", [" Hello ", ""])
async def test_disabled_timestamps_omit_metadata_and_automatic_language(
    audio: Path, text: str
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"text": text})

    client = GroqClient("https://api.groq.com", "secret", httpx.MockTransport(respond))
    result = await client.transcribe_file(audio, MODEL, TranscriptionOptions(timestamps=False))
    assert result == ((Segment("Hello"),) if text else ())
    assert form_field(requests[0], "response_format") == "json"
    assert form_field(requests[0], "language") is None
    assert form_field(requests[0], "timestamp_granularities[]") is None


@run_async
@pytest.mark.parametrize("both", [False, True])
async def test_word_responses_are_parsed_and_segments_take_precedence(
    audio: Path, both: bool
) -> None:
    requests: list[httpx.Request] = []
    body: dict[str, object] = {"text": "Hi.", "words": [{"word": "Hi.", "start": 0, "end": 1}]}
    if both:
        body["segments"] = [{"text": "Hi.", "start": 0, "end": 2}]

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body)

    client = GroqClient("https://api.groq.com", "secret", httpx.MockTransport(respond))
    result = await client.transcribe_file(audio, MODEL, TranscriptionOptions())
    assert result == (Segment("Hi.", timedelta(0), timedelta(seconds=2 if both else 1)),)
    assert form_field(requests[0], "timestamp_granularities[]") == "segment"


@run_async
@pytest.mark.parametrize(
    "body", [{}, {"text": 42}, {"segments": [{"text": "Hi", "start": -1, "end": 2}]}]
)
async def test_malformed_response_is_reported(audio: Path, body: dict[str, object]) -> None:
    client = GroqClient(
        "https://api.groq.com",
        "secret",
        httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
    )
    with pytest.raises(TranscriptionError, match="does not understand"):
        await client.transcribe_file(audio, MODEL, TranscriptionOptions())


@run_async
@pytest.mark.parametrize(
    "status",
    [
        pytest.param(400, id="request-detail"),
        pytest.param(401, id="authentication"),
        pytest.param(429, id="rate-limit"),
        pytest.param(500, id="server-failure"),
    ],
)
async def test_sdk_errors_are_redacted_and_only_service_failures_retry(
    audio: Path, status: int
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "bad secret"}})

    client = GroqClient("https://api.groq.com", "secret", httpx.MockTransport(respond))
    with pytest.raises(TranscriptionError) as caught:
        await client.transcribe_file(audio, MODEL, TranscriptionOptions())
    assert "secret" not in str(caught.value)
    assert caught.value.retryable is (status in (429, 500))
    assert len(requests) == 1


@run_async
async def test_rejection_shows_the_service_message_not_the_raw_response(audio: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Invalid file format"}})

    client = GroqClient("https://api.groq.com", "secret", httpx.MockTransport(respond))
    with pytest.raises(TranscriptionError) as caught:
        await client.transcribe_file(audio, MODEL, TranscriptionOptions())
    assert str(caught.value).endswith("Details: Invalid file format")
    assert "Error code" not in str(caught.value)


@run_async
@pytest.mark.parametrize("is_timeout", [False, True])
async def test_network_failures_are_retryable(audio: Path, is_timeout: bool) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if is_timeout:
            raise httpx.ReadTimeout("secret", request=request)
        raise httpx.ConnectError("secret", request=request)

    client = GroqClient("https://api.groq.com", "secret", httpx.MockTransport(respond))
    with pytest.raises(TranscriptionError) as caught:
        await client.transcribe_file(audio, MODEL, TranscriptionOptions())
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

    client = GroqClient("https://api.groq.com", "secret", WaitingTransport())
    task = asyncio.create_task(client.transcribe_file(audio, MODEL, TranscriptionOptions()))
    await asyncio.wait_for(started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@run_async
async def test_realtime_is_explicitly_unsupported() -> None:
    with pytest.raises(TranscriptionError, match="file transcription only"):
        await GroqClient("https://api.groq.com", "secret").open_realtime(
            MODEL, TranscriptionOptions()
        )


def test_groq_configuration_roundtrips_with_its_upload_limits(tmp_path: Path) -> None:
    assert validate_provider("Groq", ApiKind.GROQ, "") is None
    provider = Provider(
        "groq", "Groq", ApiKind.GROQ, effective_base_url(ApiKind.GROQ, ""), (MODEL,)
    )
    store = SettingsStore(tmp_path / "settings.json")
    store.save_state(Settings((provider,)))
    assert store.load_state().providers == (provider,)
    policy = descriptor(ApiKind.GROQ).upload
    assert policy.bytes == 25_000_000 and policy.format is AudioFormat.OGG
    assert effective_upload_limits(
        GroqClient(provider.base_url, "secret").upload_limits(MODEL, TranscriptionOptions()),
        provider.upload,
        ApiKind.GROQ,
    ) == UploadLimits(25_000_000, format=OGG_OPUS)


@pytest.mark.parametrize("model_id", ["whisper-large-v3", "whisper-large-v3-turbo"])
def test_whisper_capabilities_support_timestamps_without_live_or_speakers(model_id: str) -> None:
    from kikoe.core.models import Capabilities, ModeCapabilities

    catalog = load_catalog()
    caps = catalog.preset(ApiKind.GROQ, model_id).defaults()
    assert caps == Capabilities(file=ModeCapabilities(timestamps=True))
    claimed = ModelSpec(
        model_id,
        model_id,
        Capabilities(
            file=ModeCapabilities(True, True, True), realtime=ModeCapabilities(True, False)
        ),
    )
    assert catalog.clamp(ApiKind.GROQ, claimed).capabilities == caps
