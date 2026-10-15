"""The local client asks the runtime for the model and the engine's worker."""

from datetime import timedelta
from pathlib import Path

import pytest

from helpers import file_caps, run_async
from kikoe.core.local_models import Download, LocalModelSpec, ModelRole
from kikoe.core.models import ModelSpec, Segment
from kikoe.core.upload import WAV
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.local.client import LocalClient
from kikoe.providers.local.connection import EngineConnection, transcription_error
from kikoe.providers.local.protocol import ErrorKind
from kikoe.providers.local.worker_host import WorkerError

SPEC = LocalModelSpec(
    "tiny",
    "engine",
    "Tiny",
    "Family",
    ModelRole.ASR,
    Download("https://files.test/tiny.gguf", "a" * 64, 1),
    (),
    timestamps=True,
    max_seconds=600,
)
MODEL = ModelSpec("tiny", "Tiny", file_caps(timestamps=True))


class FakeConnection(EngineConnection):
    def __init__(self, error: WorkerError | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.error = error

    async def transcribe(
        self,
        model: Path,
        audio: Path,
        *,
        device: str | None,
        language: str | None,
        timestamps: bool,
    ) -> tuple[Segment, ...]:
        self.calls.append(
            {
                "model": model,
                "audio": audio,
                "device": device,
                "language": language,
                "ts": timestamps,
            }
        )
        if self.error is not None:
            raise transcription_error(self.error)
        return (Segment("hi", timedelta(0), timedelta(seconds=1)),)


class FakeRuntime:
    def __init__(self, installed: bool = True, error: WorkerError | None = None) -> None:
        self.installed = installed
        self.engine = FakeConnection(error)

    def installed_model(self, model_id: str) -> tuple[LocalModelSpec, Path] | None:
        return (SPEC, Path("/models/tiny.gguf")) if self.installed and model_id == "tiny" else None

    def connection(self, engine_id: str) -> EngineConnection:
        return self.engine

    def device(self, engine_id: str) -> str | None:
        return "vulkan:1"


@run_async
async def test_files_are_sent_to_the_engine_with_the_chosen_device() -> None:
    runtime = FakeRuntime()
    client = LocalClient(runtime)
    segments = await client.transcribe_file(
        Path("/tmp/a.wav"), MODEL, TranscriptionOptions(language="en-GB")
    )
    assert segments == (Segment("hi", timedelta(0), timedelta(seconds=1)),)
    assert runtime.engine.calls == [
        {
            "model": Path("/models/tiny.gguf"),
            "audio": Path("/tmp/a.wav"),
            "device": "vulkan:1",
            "language": "en-GB",
            "ts": True,
        }
    ]


@run_async
async def test_timestamps_are_not_requested_when_turned_off() -> None:
    runtime = FakeRuntime()
    await LocalClient(runtime).transcribe_file(
        Path("/tmp/a.wav"), MODEL, TranscriptionOptions(timestamps=False)
    )
    assert runtime.engine.calls[0]["ts"] is False


@run_async
async def test_a_removed_model_is_explained() -> None:
    with pytest.raises(TranscriptionError, match="no longer downloaded"):
        await LocalClient(FakeRuntime(installed=False)).transcribe_file(
            Path("/tmp/a.wav"), MODEL, TranscriptionOptions()
        )


@run_async
async def test_a_crashed_engine_is_a_retryable_failure() -> None:
    client = LocalClient(FakeRuntime(error=WorkerError(ErrorKind.ENGINE)))
    with pytest.raises(TranscriptionError) as caught:
        await client.transcribe_file(Path("/tmp/a.wav"), MODEL, TranscriptionOptions())
    assert caught.value.retryable


def test_uploads_are_wav_split_at_the_model_limit() -> None:
    limits = LocalClient(FakeRuntime()).upload_limits(MODEL, TranscriptionOptions())
    assert limits.format == WAV
    assert limits.duration == timedelta(seconds=600)


@run_async
async def test_a_file_only_model_refuses_live_transcription() -> None:
    with pytest.raises(TranscriptionError, match="files only"):
        await LocalClient(FakeRuntime()).open_realtime(MODEL, TranscriptionOptions())
