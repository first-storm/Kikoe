"""Audio LLM in Gemini format sends inline audio and a prompt to generateContent."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from kikoe.core.models import LlmFormat, LlmSettings, ModelSpec, Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.providers.audio_llm_gemini import AudioLlmGeminiClient
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from tests.helpers import file_caps, run_async

MODEL = ModelSpec("gemini-3-flash", "Gemini Flash", file_caps())
STREAMING = ModelSpec(MODEL.model_id, MODEL.display_name, file_caps(streaming=True))
LLM = LlmSettings(LlmFormat.GEMINI, "Transcribe.")


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    """MP3 is the default upload format of this API."""
    path = tmp_path / "upload.mp3"
    path.write_bytes(b"ID3 fake audio")
    return path


def reply(text: str, reason: str | None = "STOP") -> dict[str, object]:
    candidate: dict[str, object] = {"content": {"role": "model", "parts": [{"text": text}]}}
    if reason is not None:
        candidate["finishReason"] = reason
    return {"candidates": [candidate]}


def client(respond: httpx.MockTransport) -> AudioLlmGeminiClient:
    return AudioLlmGeminiClient("https://example.test", "fake-key", LLM, respond)


@run_async
async def test_sends_inline_wav_and_prompt(tmp_path: Path) -> None:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFFaudio")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=reply(" 你好。 "))

    result = await client(httpx.MockTransport(respond)).transcribe_file(
        audio, MODEL, TranscriptionOptions(language="zh-CN")
    )

    assert result == (Segment("你好。"),)
    assert requests[0].url.path.endswith(f"/models/{MODEL.model_id}:generateContent")
    assert requests[0].headers["x-goog-api-key"] == "fake-key"
    sound, prompt = json.loads(requests[0].content)["contents"][0]["parts"]
    assert sound["inlineData"]["data"] == base64.b64encode(b"RIFFaudio").decode()
    assert "audio/wav" in sound["inlineData"].values()
    assert prompt["text"].startswith("Transcribe.")
    assert "zh-CN" in prompt["text"]


@pytest.mark.parametrize(
    ("reason", "message"),
    [("MAX_TOKENS", "chunk length or size"), ("SAFETY", "content filter")],
)
@run_async
async def test_unsuccessful_finish_is_explained(audio: Path, reason: str, message: str) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply("x", reason)))
    with pytest.raises(TranscriptionError, match=message) as error:
        await client(transport).transcribe_file(audio, MODEL, TranscriptionOptions())
    assert not error.value.retryable


@run_async
async def test_blocked_prompt_counts_as_filtered(audio: Path) -> None:
    body = {"promptFeedback": {"blockReason": "SAFETY"}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    with pytest.raises(TranscriptionError, match="content filter"):
        await client(transport).transcribe_file(audio, MODEL, TranscriptionOptions())


@run_async
async def test_stream_previews_text_and_commits_after_stop(audio: Path) -> None:
    events = [reply("Hello", None), reply(" world", None), reply(".")]
    body = "".join(f"data: {json.dumps(event)}\r\n\r\n" for event in events)
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    updates: list[FileTranscriptSnapshot] = []
    result = await client(httpx.MockTransport(respond)).transcribe_file(
        audio, STREAMING, TranscriptionOptions(), on_update=updates.append
    )

    assert result == (Segment("Hello world."),)
    assert requests[0].url.path.endswith(":streamGenerateContent")
    assert updates[0] == FileTranscriptSnapshot(preview=(Segment("Hello"),))
    assert updates[-1] == FileTranscriptSnapshot((Segment("Hello world."),))


@run_async
async def test_stream_ending_without_stop_is_retryable(audio: Path) -> None:
    body = f"data: {json.dumps(reply('partial', None))}\r\n\r\n"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=body
        )
    )
    with pytest.raises(TranscriptionError) as error:
        await client(transport).transcribe_file(audio, STREAMING, TranscriptionOptions())
    assert error.value.retryable


@run_async
async def test_mp3_chunk_is_labelled_audio_mpeg(audio: Path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=reply("ok"))

    await client(httpx.MockTransport(respond)).transcribe_file(audio, MODEL, TranscriptionOptions())
    sound = json.loads(requests[0].content)["contents"][0]["parts"][0]
    assert "audio/mpeg" in sound["inlineData"].values()
