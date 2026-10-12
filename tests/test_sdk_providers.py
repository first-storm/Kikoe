"""Official adapters normalize metadata and enforce model-dependent limits."""

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from helpers import run_async
from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Provider, Segment
from kikoe.providers.base import TranscriptionOptions
from kikoe.providers.deepgram import DeepgramClient, parse_deepgram
from kikoe.providers.factory import create_client
from kikoe.providers.gemini import GeminiClient, parse_gemini
from kikoe.providers.openrouter import OpenRouterClient
from kikoe.providers.parsing import parse_segments


def test_deepgram_word_speaker_zero_is_preserved() -> None:
    body = {
        "results": {
            "channels": [
                {
                    "alternatives": [
                        {
                            "transcript": "Hello.",
                            "words": [
                                {
                                    "punctuated_word": "Hello.",
                                    "start": 1.0,
                                    "end": 2.0,
                                    "speaker": 0,
                                }
                            ],
                        }
                    ]
                }
            ]
        }
    }
    assert parse_deepgram(body, True, True) == (
        Segment("Hello.", timedelta(seconds=1), timedelta(seconds=2), "0"),
    )
    assert parse_deepgram(body, False, False) == (Segment("Hello."),)


def test_gemini_word_offsets_and_speakers_are_normalized() -> None:
    body = {
        "steps": [
            {
                "content": [
                    {
                        "annotations": [
                            {
                                "type": "word_info",
                                "text": "Hello.",
                                "start_offset": "0.100s",
                                "end_offset": "1.200s",
                                "speaker": "spk_1",
                            }
                        ]
                    }
                ]
            }
        ]
    }
    assert parse_gemini(body, True, True) == (
        Segment("Hello.", timedelta(milliseconds=100), timedelta(milliseconds=1200), "spk_1"),
    )


def test_gemini_plain_result_never_invents_timestamps() -> None:
    assert parse_gemini({"output_text": "Hello"}, True, False) == (Segment("Hello"),)
    assert parse_gemini({"output_text": ""}, False, False) == ()


def test_gemini_annotation_options_reduce_upload_duration() -> None:
    client = GeminiClient("https://example.test", "fake")
    model = ModelSpec(
        "gemini-3.5-transcribe", "Gemini", Capabilities(timestamps=True, diarization=True)
    )
    assert client.upload_limits(model, TranscriptionOptions()).duration == timedelta(minutes=30)
    assert client.upload_limits(model, TranscriptionOptions(False, False)).duration == timedelta(
        minutes=60
    )


@pytest.mark.parametrize(
    ("api", "kind"),
    [
        (ApiKind.DEEPGRAM, DeepgramClient),
        (ApiKind.GEMINI, GeminiClient),
        (ApiKind.OPENROUTER, OpenRouterClient),
    ],
)
def test_factory_selects_official_adapters(api: ApiKind, kind: type[object]) -> None:
    assert isinstance(
        create_client(Provider("p", "Provider", api, "https://example.test"), "fake"), kind
    )


def test_diarized_segments_discard_unrequested_metadata() -> None:
    data = {"segments": [{"text": "Hello.", "start": 1, "end": 2, "speaker": "A"}]}
    assert parse_segments(data, True, True) == (
        Segment("Hello.", timedelta(seconds=1), timedelta(seconds=2), "A"),
    )
    assert parse_segments(data, False, False) == (Segment("Hello."),)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_invalid_provider_timestamps_are_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        parse_segments({"segments": [{"text": "Hello", "start": value, "end": 2}]}, True, False)


@run_async
async def test_openrouter_sdk_encodes_diarization_and_parses_word_speakers(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "text": "Hello.",
                "words": [{"word": "Hello.", "start": 0, "end": 1, "speaker": 0}],
            },
        )

    audio = tmp_path / "clip.ogg"
    audio.write_bytes(b"fake ogg")
    client = OpenRouterClient(
        "https://example.test/api/v1", "fake-key", httpx.MockTransport(respond)
    )
    model = ModelSpec("deepgram/nova-3", "Nova", Capabilities(timestamps=True, diarization=True))
    result = await client.transcribe_file(audio, model, TranscriptionOptions())
    assert result == (Segment("Hello.", timedelta(), timedelta(seconds=1), "0"),)
    assert requests[0].url.path == "/api/v1/audio/transcriptions"
    assert requests[0].headers["authorization"] == "Bearer fake-key"
    assert b'name="diarize"' in requests[0].content
    assert b"verbose_json" in requests[0].content


@run_async
async def test_deepgram_sdk_uses_one_v1_prefix_and_requested_options(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": {
                    "channels": [
                        {
                            "alternatives": [
                                {
                                    "transcript": "Hello.",
                                    "words": [
                                        {"word": "Hello.", "start": 0, "end": 1, "speaker": 0}
                                    ],
                                }
                            ]
                        }
                    ]
                }
            },
        )

    audio = tmp_path / "clip.ogg"
    audio.write_bytes(b"fake ogg")
    client = DeepgramClient("https://example.test/v1", "fake-key", httpx.MockTransport(respond))
    result = await client.transcribe_file(
        audio, ModelSpec("nova-3", "Nova", Capabilities(False, True, True)), TranscriptionOptions()
    )
    assert result == (Segment("Hello.", timedelta(), timedelta(seconds=1), "0"),)
    assert requests[0].url.path == "/v1/listen"
    assert requests[0].url.params["diarize_model"] == "latest"
    assert requests[0].headers["authorization"] == "Token fake-key"


@run_async
async def test_gemini_sdk_uploads_transcribes_and_deletes_remote_file(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        if request.url.path.startswith("/upload/"):
            return httpx.Response(
                200, headers={"x-goog-upload-url": "https://example.test/resumable"}
            )
        if request.url.path == "/resumable":
            return httpx.Response(
                200,
                headers={"x-goog-upload-status": "final"},
                json={
                    "file": {
                        "name": "files/fake",
                        "uri": "https://example.test/files/fake",
                        "mimeType": "audio/ogg",
                        "state": "ACTIVE",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "status": "completed",
                "steps": [
                    {"type": "model_output", "content": [{"type": "text", "text": "Hello."}]}
                ],
            },
        )

    audio = tmp_path / "clip.ogg"
    audio.write_bytes(b"fake ogg")
    client = GeminiClient("https://example.test", "fake-key", httpx.MockTransport(respond))
    result = await client.transcribe_file(
        audio,
        ModelSpec("gemini-3.5-transcribe", "Gemini", Capabilities(False, True, True)),
        TranscriptionOptions(),
    )
    assert result == (Segment("Hello."),)
    request = next(r for r in requests if r.url.path.endswith("/interactions"))
    body = json.loads(request.content)
    assert (
        body["generation_config"]["transcription_config"]["mode"]["diarization_mode"] == "speaker"
    )
    assert requests[-1].method == "DELETE"


def test_gemini_word_byte_indices_recover_text_for_multibyte_audio_transcripts() -> None:
    body = {
        "output_text": "你好",
        "steps": [
            {
                "content": [
                    {
                        "text": "你好",
                        "annotations": [
                            {
                                "type": "word_info",
                                "start_index": 0,
                                "end_index": 6,
                                "speaker": "one",
                            }
                        ],
                    }
                ]
            }
        ],
    }
    assert parse_gemini(body, False, True) == (Segment("你好", speaker_id="one"),)


def test_gemini_missing_word_text_does_not_discard_plain_transcript() -> None:
    body = {
        "output_text": "Hello",
        "steps": [{"content": [{"annotations": [{"type": "word_info"}]}]}],
    }
    assert parse_gemini(body, True, False) == (Segment("Hello"),)


def test_aistudio_adapter_ignores_vertexai_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    with GeminiClient("https://example.test", "fake")._sdk() as sdk:
        assert not sdk.vertexai
