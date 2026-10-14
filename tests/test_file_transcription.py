"""The workflow prepares the client and decides what a failure leaves behind."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import file_caps, run_async
from kikoe.core.choice import ModelChoice
from kikoe.core.models import (
    ApiKind,
    ModelSpec,
    Note,
    Provider,
    Recording,
    RecordingSource,
    Segment,
    TranscriptionState,
)
from kikoe.providers.base import TranscriptionError, TranscriptionOptions, TranscriptionProvider
from kikoe.services import file_transcription
from kikoe.services.file_transcription import FileTranscriptionWorkflow
from kikoe.storage.notes import NoteRepository

MODEL = ModelSpec("whisper-1", "Whisper", file_caps(timestamps=True))
PROVIDER = Provider("p1", "OpenAI", ApiKind.OPENAI, "https://example.test/v1", (MODEL,))
CHOICE = ModelChoice(PROVIDER, MODEL)
OPTIONS = TranscriptionOptions(timestamps=False, diarization=False)
NOTE = Note("n1", "Meeting", datetime.now(UTC))
RECORDING = Recording(
    "r1",
    datetime.now(UTC),
    timedelta(seconds=3),
    RecordingSource.RECORDED,
    "OpenAI",
    "whisper-1",
    audio_path=Path("r1.flac"),
)


class Keys:
    def __init__(self, key: str = "secret") -> None:
        self.key = key
        self.requested: list[str] = []

    async def get_api_key(self, provider_id: str) -> str:
        self.requested.append(provider_id)
        return self.key


class Clients:
    """Hands out a marker object and remembers what it was asked to build."""

    def __init__(self) -> None:
        self.created: list[tuple[Provider, str]] = []
        self.client = object()

    def __call__(self, provider: Provider, api_key: str) -> TranscriptionProvider:
        self.created.append((provider, api_key))
        return self.client  # pyright: ignore[reportReturnType] # the stubs below only compare identity


class Calls:
    """Replaces the importer's functions and records their arguments."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.imports: list[dict[str, object]] = []
        self.transcriptions: list[dict[str, object]] = []

    async def import_audio(self, *args: object, **kwargs: object) -> Note:
        self.imports.append({"args": args, **kwargs})
        if self.error:
            raise self.error
        return NOTE

    async def transcribe_audio(
        self, *args: object, **kwargs: object
    ) -> tuple[timedelta, tuple[Segment, ...]]:
        self.transcriptions.append({"args": args, **kwargs})
        if self.error:
            raise self.error
        return timedelta(seconds=3), (Segment("Hello"),)


class Marks(list[TranscriptionState]):
    """Stands in for ``NoteRepository.update_transcription`` and records each state."""

    async def __call__(
        self, _note_id: str, _recording_id: str, state: TranscriptionState, **_kwargs: object
    ) -> Note:
        self.append(state)
        return NOTE


@pytest.fixture
def marks(notes: NoteRepository, monkeypatch: pytest.MonkeyPatch) -> Marks:
    marks = Marks()
    monkeypatch.setattr(notes, "update_transcription", marks)
    return marks


def install(monkeypatch: pytest.MonkeyPatch, calls: Calls) -> None:
    monkeypatch.setattr(file_transcription, "import_audio", calls.import_audio)
    monkeypatch.setattr(file_transcription, "transcribe_audio", calls.transcribe_audio)


def no_progress(*_args: object) -> None:
    pass


@run_async
async def test_import_builds_the_client_from_the_providers_stored_key(
    notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, keys, clients = Calls(), Keys("sk-1"), Clients()
    install(monkeypatch, calls)
    workflow = FileTranscriptionWorkflow(keys, notes, clients)
    result = await workflow.import_file(
        Path("talk.m4a"), None, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
    )
    assert result is NOTE
    assert keys.requested == ["p1"]
    assert clients.created == [(PROVIDER, "sk-1")]
    assert calls.imports[0]["client"] is clients.client
    assert calls.imports[0]["options"] is OPTIONS


@run_async
async def test_retranscription_commits_only_the_result(
    marks: Marks, notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, clients = Calls(), Clients()
    install(monkeypatch, calls)
    workflow = FileTranscriptionWorkflow(Keys(), notes, clients)
    await workflow.retranscribe(
        "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
    )
    assert calls.transcriptions[0]["client"] is clients.client
    assert calls.transcriptions[0]["options"] is OPTIONS
    assert marks == [TranscriptionState.READY]


@pytest.mark.parametrize("error", [TranscriptionError("Invalid API key"), RuntimeError("bug")])
@run_async
async def test_retranscription_keeps_the_existing_state_when_it_fails(
    error: Exception, marks: Marks, notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Calls(error))
    workflow = FileTranscriptionWorkflow(Keys(), notes, Clients())
    with pytest.raises(type(error)):
        await workflow.retranscribe(
            "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
        )
    assert marks == []


@run_async
async def test_a_pending_transcription_is_marked_pending_then_ready(
    marks: Marks, notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Calls())
    workflow = FileTranscriptionWorkflow(Keys(), notes, Clients())
    await workflow.transcribe_pending(
        "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
    )
    assert marks == [TranscriptionState.PENDING, TranscriptionState.READY]


@pytest.mark.parametrize("error", [TranscriptionError("Invalid API key"), RuntimeError("bug")])
@run_async
async def test_a_failed_pending_transcription_is_marked_failed_once_and_reraises(
    error: Exception, marks: Marks, notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(monkeypatch, Calls(error))
    workflow = FileTranscriptionWorkflow(Keys(), notes, Clients())
    with pytest.raises(type(error)):
        await workflow.transcribe_pending(
            "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
        )
    assert marks == [TranscriptionState.PENDING, TranscriptionState.FAILED]


@run_async
async def test_a_key_store_error_before_uploading_also_marks_failure(
    marks: Marks, notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenKeys:
        async def get_api_key(self, provider_id: str) -> str:
            raise TranscriptionError(f"No key for {provider_id}")

    calls = Calls()
    install(monkeypatch, calls)
    workflow = FileTranscriptionWorkflow(BrokenKeys(), notes, Clients())
    with pytest.raises(TranscriptionError):
        await workflow.transcribe_pending(
            "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
        )
    assert calls.transcriptions == []
    assert marks == [TranscriptionState.PENDING, TranscriptionState.FAILED]
