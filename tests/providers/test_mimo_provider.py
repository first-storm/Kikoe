"""MiMo sends audio inside a chat completion; check the request and error handling."""

import base64
import json
from pathlib import Path

import httpx2 as httpx
import pytest

from kikoe.core.models import ApiKind, AudioFormat, ModelSpec, Segment
from kikoe.core.provider_registry import descriptor
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.mimo import MiMoClient, asr_language
from tests.helpers import file_caps, run_async
from tests.providers.helpers import completion

MODEL = ModelSpec("mimo-v2.5-asr", "MiMo ASR", file_caps())


@pytest.mark.parametrize(
    ("tag", "expected"),
    [(None, "auto"), ("zh", "zh"), ("zh-CN", "zh"), ("en-US", "en"), ("ja", "auto")],
)
def test_asr_language_falls_back_to_automatic_detection(tag: str | None, expected: str) -> None:
    assert asr_language(tag) == expected


def test_mimo_uploads_only_mp3_or_wav_within_the_base64_limit() -> None:
    policy = descriptor(ApiKind.MIMO).upload
    assert policy.formats == (AudioFormat.MP3, AudioFormat.WAV)
    assert policy.bytes * 4 / 3 < 10 * 1024 * 1024


@run_async
async def test_mimo_sends_audio_as_input_audio_and_returns_text(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=completion(" 你好。 "))

    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFFaudio")
    client = MiMoClient("https://example.test/v1", "fake-key", httpx.MockTransport(respond))
    result = await client.transcribe_file(audio, MODEL, TranscriptionOptions(language="zh-CN"))

    assert result == (Segment("你好。"),)
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer fake-key"
    body = json.loads(requests[0].content)
    assert body["stream"] is False
    assert body["model"] == "mimo-v2.5-asr"
    assert body["asr_options"] == {"language": "zh"}
    [part] = body["messages"][0]["content"]
    encoded = base64.b64encode(b"RIFFaudio").decode()
    assert part == {
        "type": "input_audio",
        "input_audio": {"data": f"data:audio/wav;base64,{encoded}"},
    }


@run_async
async def test_mimo_error_does_not_leak_the_api_key(tmp_path: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad audio for secret-key"}})

    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"x")
    client = MiMoClient("https://example.test/v1", "secret-key", httpx.MockTransport(respond))
    with pytest.raises(TranscriptionError) as error:
        await client.transcribe_file(audio, MODEL, TranscriptionOptions())
    assert "secret-key" not in str(error.value)
    assert "bad audio for [redacted]" in str(error.value)
    assert not error.value.retryable


@run_async
async def test_mimo_unauthorized_asks_for_a_new_key(tmp_path: Path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"x")
    client = MiMoClient(
        "https://example.test/v1",
        "k",
        httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {}})),
    )
    with pytest.raises(TranscriptionError, match="API key"):
        await client.transcribe_file(audio, MODEL, TranscriptionOptions())


@run_async
async def test_mimo_empty_reply_is_an_error(tmp_path: Path) -> None:
    audio = tmp_path / "clip.mp3"
    audio.write_bytes(b"x")
    client = MiMoClient(
        "https://example.test/v1",
        "k",
        httpx.MockTransport(lambda request: httpx.Response(200, json=completion(None))),
    )
    with pytest.raises(TranscriptionError):
        await client.transcribe_file(audio, MODEL, TranscriptionOptions())


@run_async
async def test_mimo_has_no_realtime_mode() -> None:
    with pytest.raises(TranscriptionError, match="MiMo supports file transcription only"):
        await MiMoClient("https://example.test/v1", "k").open_realtime(
            MODEL, TranscriptionOptions()
        )
