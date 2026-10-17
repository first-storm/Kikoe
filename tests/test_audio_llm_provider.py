"""Audio LLM sends audio and a prompt as a chat completion; check request and replies."""

import base64
import json
from pathlib import Path

import httpx2 as httpx
import pytest

from helpers import file_caps, run_async
from kikoe.core.llm_prompt import DEFAULT_PROMPT
from kikoe.core.models import LlmSettings, ModelSpec, Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.providers.audio_llm import AudioLlmClient
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from test_chat_file_stream import EventBytes, chunk
from test_mimo_provider import completion

MODEL = ModelSpec("gpt-4o-audio-preview", "GPT-4o Audio", file_caps())
STREAMING = ModelSpec(MODEL.model_id, MODEL.display_name, file_caps(streaming=True))
DEFAULT_LLM = LlmSettings()


def client(
    respond: httpx.MockTransport, llm: LlmSettings = DEFAULT_LLM, key: str = "secret"
) -> AudioLlmClient:
    return AudioLlmClient("https://example.test/v1", key, llm, respond)


def replying(body: dict[str, object], requests: list[httpx.Request]) -> httpx.MockTransport:
    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(respond)


@run_async
async def test_sends_prompt_and_wav_audio_and_returns_text(tmp_path: Path) -> None:
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFFaudio")
    requests: list[httpx.Request] = []
    sdk = client(replying(completion("```\n 你好。\n```"), requests))

    result = await sdk.transcribe_file(audio, MODEL, TranscriptionOptions(language="zh-CN"))

    assert result == (Segment("你好。"),)
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer secret"
    body = json.loads(requests[0].content)
    assert body["stream"] is False
    assert body["model"] == MODEL.model_id
    text, sound = body["messages"][0]["content"]
    assert text["type"] == "text"
    assert text["text"].startswith(DEFAULT_PROMPT)
    assert "zh-CN" in text["text"]
    assert sound == {
        "type": "input_audio",
        "input_audio": {"data": base64.b64encode(b"RIFFaudio").decode(), "format": "wav"},
    }


@run_async
async def test_custom_prompt_replaces_the_default(audio: Path) -> None:
    requests: list[httpx.Request] = []
    sdk = client(replying(completion("ok"), requests), LlmSettings(prompt="只输出文字。"))
    await sdk.transcribe_file(audio, MODEL, TranscriptionOptions())
    text = json.loads(requests[0].content)["messages"][0]["content"][0]["text"]
    assert text == "只输出文字。"


@run_async
async def test_local_server_without_a_key_is_allowed(audio: Path) -> None:
    requests: list[httpx.Request] = []
    sdk = client(replying(completion("ok"), requests), key="")
    assert await sdk.transcribe_file(audio, MODEL, TranscriptionOptions()) == (Segment("ok"),)
    assert "authorization" not in requests[0].headers


@run_async
async def test_silence_answered_with_nothing_is_an_empty_transcript(audio: Path) -> None:
    sdk = client(replying(completion(""), []))
    assert await sdk.transcribe_file(audio, MODEL, TranscriptionOptions()) == ()


@run_async
async def test_output_limit_asks_for_smaller_chunks(audio: Path) -> None:
    body = completion("partial")
    body["choices"] = [
        {"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "x"}}
    ]
    with pytest.raises(TranscriptionError, match="chunk length or size") as error:
        await client(replying(body, [])).transcribe_file(audio, MODEL, TranscriptionOptions())
    assert not error.value.retryable


@run_async
async def test_error_does_not_leak_the_api_key(audio: Path) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "audio rejected for secret-key"}})

    sdk = client(httpx.MockTransport(respond), key="secret-key")
    with pytest.raises(TranscriptionError) as error:
        await sdk.transcribe_file(audio, MODEL, TranscriptionOptions())
    assert "secret-key" not in str(error.value)


@run_async
async def test_stream_previews_text_and_commits_after_stop(audio: Path) -> None:
    events = EventBytes([chunk("Hello"), chunk(" world"), chunk(".", "stop")])

    def respond(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=events)

    updates: list[FileTranscriptSnapshot] = []
    result = await client(httpx.MockTransport(respond)).transcribe_file(
        audio, STREAMING, TranscriptionOptions(), on_update=updates.append
    )
    assert result == (Segment("Hello world."),)
    assert updates[0] == FileTranscriptSnapshot(preview=(Segment("Hello"),))
    assert updates[-1] == FileTranscriptSnapshot((Segment("Hello world."),))
    assert events.closed


@run_async
async def test_stream_ending_without_stop_is_retryable(audio: Path) -> None:
    events = EventBytes([chunk("partial")])

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=events)

    with pytest.raises(TranscriptionError) as error:
        await client(httpx.MockTransport(respond)).transcribe_file(
            audio, STREAMING, TranscriptionOptions()
        )
    assert error.value.retryable
