"""The local client asks the runtime for the model and the engine's worker."""

from datetime import timedelta
from pathlib import Path

import pytest

from helpers import file_caps, run_async
from kikoe.core.diarization import SpeakerTurn, TimedWord
from kikoe.core.local_models import Download, LocalModelSpec, ModelRole, model_spec
from kikoe.core.models import ModelSpec, Segment
from kikoe.core.upload import WAV
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.local.client import LocalClient
from kikoe.providers.local.connection import (
    EngineConnection,
    EngineTranscript,
    transcription_error,
)
from kikoe.providers.local.engines import load_catalog
from kikoe.providers.local.protocol import ErrorKind
from kikoe.providers.local.session import LocalSession
from kikoe.providers.local.worker_host import WorkerError, WorkerHost

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
        self.diarized: Path | None = None
        self.error = error

    async def transcribe(
        self,
        model: Path,
        audio: Path,
        *,
        device: str | None,
        language: str | None,
        timestamps: bool,
        words: bool = False,
        diarization: bool = False,
    ) -> EngineTranscript:
        call = {"model": model, "audio": audio, "device": device, "language": language}
        self.calls.append({**call, "ts": timestamps, "diarization": diarization})
        if self.error is not None:
            raise transcription_error(self.error)
        segment = Segment(
            "hi there",
            timedelta(0) if timestamps else None,
            timedelta(seconds=2) if timestamps else None,
            "1" if diarization else None,
        )
        if not words:
            return EngineTranscript((segment,))
        return EngineTranscript(
            (segment,),
            (
                TimedWord("hi", timedelta(0), timedelta(seconds=1)),
                TimedWord("there", timedelta(seconds=1), timedelta(seconds=2)),
            ),
        )

    async def diarize(
        self, model: Path, audio: Path, *, device: str | None
    ) -> tuple[SpeakerTurn, ...]:
        self.diarized = model
        return (
            SpeakerTurn(timedelta(0), timedelta(seconds=1), "1"),
            SpeakerTurn(timedelta(seconds=1), timedelta(seconds=2), "2"),
        )


class FakeRuntime:
    def __init__(
        self, installed: bool = True, error: WorkerError | None = None, diarizer: bool = False
    ) -> None:
        self.installed = installed
        self.has_diarizer = diarizer
        self.engine = FakeConnection(error)

    def installed_model(self, model_id: str) -> tuple[LocalModelSpec, Path] | None:
        return (SPEC, Path("/models/tiny.gguf")) if self.installed and model_id == "tiny" else None

    def connection(self, engine_id: str) -> EngineConnection:
        return self.engine

    def device(self, engine_id: str) -> str | None:
        return "vulkan:1"

    def diarizer(self, engine_id: str) -> Path | None:
        return Path("/models/speakers.gguf") if self.has_diarizer else None


@run_async
async def test_files_are_sent_to_the_engine_with_the_chosen_device() -> None:
    runtime = FakeRuntime()
    client = LocalClient(runtime)
    segments = await client.transcribe_file(
        Path("/tmp/a.wav"), MODEL, TranscriptionOptions(language="en-GB")
    )
    assert segments == (Segment("hi there", timedelta(0), timedelta(seconds=2)),)
    assert runtime.engine.calls == [
        {
            "model": Path("/models/tiny.gguf"),
            "audio": Path("/tmp/a.wav"),
            "device": "vulkan:1",
            "language": "en-GB",
            "ts": True,
            "diarization": False,
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


@run_async
async def test_speakers_split_the_transcript_where_the_voice_changes() -> None:
    runtime = FakeRuntime(diarizer=True)
    segments = await LocalClient(runtime).transcribe_file(
        Path("/tmp/a.wav"), MODEL, TranscriptionOptions(timestamps=False, diarization=True)
    )
    assert segments == (
        Segment("hi", timedelta(0), timedelta(seconds=1), "1"),
        Segment("there", timedelta(seconds=1), timedelta(seconds=2), "2"),
    )
    assert runtime.engine.diarized == Path("/models/speakers.gguf")
    assert runtime.engine.calls[0]["ts"] is True


@run_async
async def test_no_speaker_pass_runs_unless_requested() -> None:
    runtime = FakeRuntime(diarizer=True)
    await LocalClient(runtime).transcribe_file(
        Path("/tmp/a.wav"), MODEL, TranscriptionOptions(diarization=False)
    )
    assert runtime.engine.diarized is None


class CatalogRuntime(FakeRuntime):
    """Resolve shipped models without downloading files or starting a worker."""

    def installed_model(self, model_id: str) -> tuple[LocalModelSpec, Path] | None:
        model = load_catalog().model(model_id)
        return (model, Path("models") / model.file.filename) if model else None


@pytest.mark.parametrize("language", [None, "ru", "ru-RU"])
@run_async
async def test_cohere_rejects_invalid_language_before_connecting(
    language: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = CatalogRuntime()

    def unexpected_connection(engine_id: str) -> EngineConnection:
        pytest.fail("Language must be validated before connecting to the worker")

    monkeypatch.setattr(runtime, "connection", unexpected_connection)
    spec = load_catalog().model("cohere-transcribe-03-2026")
    assert spec is not None
    with pytest.raises(TranscriptionError, match=r"[Ll]anguage"):
        await LocalClient(runtime).transcribe_file(
            Path("audio.wav"), model_spec(spec), TranscriptionOptions(language=language)
        )
    assert runtime.engine.calls == []


@pytest.mark.parametrize(
    ("model_id", "language", "sent_language", "timestamps"),
    [
        ("cohere-transcribe-03-2026", "en-GB", "en", False),
        ("cohere-transcribe-03-2026", "zh-Hans", "zh", False),
        ("qwen3-asr-0.6b", None, None, False),
        ("qwen3-asr-1.7b", "zh", "zh", False),
        ("nemotron-speech-streaming-en-0.6b", "en", "en", True),
    ],
)
@run_async
async def test_shipped_models_route_file_requests_with_their_capabilities(
    model_id: str, language: str | None, sent_language: str | None, timestamps: bool
) -> None:
    runtime = CatalogRuntime()
    spec = load_catalog().model(model_id)
    assert spec is not None
    await LocalClient(runtime).transcribe_file(
        Path("audio.wav"), model_spec(spec), TranscriptionOptions(language=language)
    )
    assert runtime.engine.calls == [
        {
            "model": Path("models") / spec.file.filename,
            "audio": Path("audio.wav"),
            "device": "vulkan:1",
            "language": sent_language,
            "ts": timestamps,
            "diarization": False,
        }
    ]


@pytest.mark.parametrize(
    ("model_id", "seconds"),
    [
        ("cohere-transcribe-03-2026", 400),
        ("qwen3-asr-0.6b", 600),
        ("qwen3-asr-1.7b", 600),
        ("moss-transcribe-diarize-q8-0", 600),
        ("moss-transcribe-diarize-q5-k-m", 600),
    ],
)
def test_shipped_models_split_long_files_at_their_configured_limit(
    model_id: str, seconds: int
) -> None:
    spec = load_catalog().model(model_id)
    assert spec is not None
    limits = LocalClient(CatalogRuntime()).upload_limits(model_spec(spec), TranscriptionOptions())
    assert limits.format == WAV
    assert limits.duration == timedelta(seconds=seconds)


@pytest.mark.parametrize("quant", ["q8-0", "q5-k-m"])
@pytest.mark.parametrize("diarizer", [False, True])
@pytest.mark.parametrize("diarization", [False, True])
@pytest.mark.parametrize("timestamps", [False, True])
@run_async
async def test_moss_native_speakers_and_times_are_independent(
    quant: str, diarizer: bool, diarization: bool, timestamps: bool
) -> None:
    runtime = CatalogRuntime(diarizer=diarizer)
    spec = load_catalog().model(f"moss-transcribe-diarize-{quant}")
    assert spec is not None
    segments = await LocalClient(runtime).transcribe_file(
        Path("audio.wav"),
        model_spec(spec),
        TranscriptionOptions(timestamps=timestamps, diarization=diarization),
    )
    assert runtime.engine.calls[0]["diarization"] is diarization
    assert runtime.engine.calls[0]["ts"] is timestamps
    assert segments[0].speaker_id == ("1" if diarization else None)
    assert segments[0].start == (timedelta(0) if timestamps else None)
    assert runtime.engine.diarized is None


@pytest.mark.parametrize("quant", ["q8-0", "q5-k-m"])
@run_async
async def test_moss_refuses_realtime_transcription(quant: str) -> None:
    spec = load_catalog().model(f"moss-transcribe-diarize-{quant}")
    assert spec is not None
    with pytest.raises(TranscriptionError, match="files only"):
        await LocalClient(CatalogRuntime()).open_realtime(model_spec(spec), TranscriptionOptions())


@run_async
async def test_nemotron_routes_live_requests_to_the_same_model_and_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = CatalogRuntime()
    calls: list[tuple[Path, str | None, str | None]] = []
    session = LocalSession(WorkerHost([]))

    async def open_stream(model: Path, *, device: str | None, language: str | None) -> LocalSession:
        calls.append((model, device, language))
        return session

    monkeypatch.setattr(runtime.engine, "open_stream", open_stream)
    spec = load_catalog().model("nemotron-speech-streaming-en-0.6b")
    assert spec is not None
    result = await LocalClient(runtime).open_realtime(
        model_spec(spec), TranscriptionOptions(language="en")
    )
    assert result is session
    assert calls == [(Path("models") / spec.file.filename, "vulkan:1", "en")]
