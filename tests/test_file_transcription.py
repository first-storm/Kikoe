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
    TranscriptionState,
)
from kikoe.providers.base import TranscriptionError, TranscriptionOptions, TranscriptionProvider
from kikoe.services import file_transcription
from kikoe.services.file_transcription import FileTranscriptionWorkflow
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository

MODEL = ModelSpec("whisper-1", "Whisper", file_caps(timestamps=True))
PROVIDER = Provider("p1", "OpenAI", ApiKind.OPENAI, "https://example.test/v1", (MODEL,))
CHOICE = ModelChoice(PROVIDER, MODEL)
OPTIONS = TranscriptionOptions(timestamps=False, diarization=False)
NOTE = Note("n1", "Meeting", datetime.now(UTC))
RECORDING = Recording(
    "r1", datetime.now(UTC), timedelta(seconds=3), RecordingSource.RECORDED, "OpenAI", "whisper-1"
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
    """Replaces the two service functions and records their arguments."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.imports: list[dict[str, object]] = []
        self.transcriptions: list[dict[str, object]] = []

    async def import_audio(self, *args: object, **kwargs: object) -> Note:
        self.imports.append({"args": args, **kwargs})
        if self.error:
            raise self.error
        return NOTE

    async def transcribe_recording(self, *args: object, **kwargs: object) -> Note:
        self.transcriptions.append({"args": args, **kwargs})
        if self.error:
            raise self.error
        return NOTE


@pytest.fixture
def notes(tmp_path: Path) -> NoteRepository:
    return NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))


def install(monkeypatch: pytest.MonkeyPatch, calls: Calls) -> None:
    monkeypatch.setattr(file_transcription, "import_audio", calls.import_audio)
    monkeypatch.setattr(file_transcription, "transcribe_recording", calls.transcribe_recording)


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
async def test_retranscription_keeps_the_existing_text_when_it_fails(
    notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls()
    install(monkeypatch, calls)
    workflow = FileTranscriptionWorkflow(Keys(), notes, Clients())
    await workflow.retranscribe(
        "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
    )
    assert calls.transcriptions[0]["preserve_on_failure"] is True


@run_async
async def test_pending_transcription_does_not_preserve_old_text(
    notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = Calls()
    install(monkeypatch, calls)
    workflow = FileTranscriptionWorkflow(Keys(), notes, Clients())
    await workflow.transcribe_pending(
        "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
    )
    assert "preserve_on_failure" not in calls.transcriptions[0]


@run_async
async def test_a_failed_pending_transcription_marks_the_recording_failed_and_reraises(
    notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    marked: list[tuple[str, str, TranscriptionState]] = []

    async def update_transcription(
        note_id: str, recording_id: str, state: TranscriptionState, **_kwargs: object
    ) -> Note:
        marked.append((note_id, recording_id, state))
        return NOTE

    monkeypatch.setattr(notes, "update_transcription", update_transcription)
    install(monkeypatch, Calls(TranscriptionError("Invalid API key")))
    workflow = FileTranscriptionWorkflow(Keys(), notes, Clients())
    with pytest.raises(TranscriptionError, match="Invalid API key"):
        await workflow.transcribe_pending(
            "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
        )
    assert marked == [("n1", "r1", TranscriptionState.FAILED)]


@run_async
async def test_a_missing_key_store_error_before_the_service_runs_also_marks_failure(
    notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    marked: list[TranscriptionState] = []

    async def update_transcription(
        _note_id: str, _recording_id: str, state: TranscriptionState, **_kwargs: object
    ) -> Note:
        marked.append(state)
        return NOTE

    class BrokenKeys:
        async def get_api_key(self, provider_id: str) -> str:
            raise TranscriptionError(f"No key for {provider_id}")

    monkeypatch.setattr(notes, "update_transcription", update_transcription)
    install(monkeypatch, Calls())
    workflow = FileTranscriptionWorkflow(BrokenKeys(), notes, Clients())
    with pytest.raises(TranscriptionError):
        await workflow.transcribe_pending(
            "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
        )
    assert marked == [TranscriptionState.FAILED]


@run_async
async def test_unexpected_errors_are_not_recorded_as_provider_failures(
    notes: NoteRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    marked: list[TranscriptionState] = []

    async def update_transcription(
        _note_id: str, _recording_id: str, state: TranscriptionState, **_kwargs: object
    ) -> Note:
        marked.append(state)
        return NOTE

    monkeypatch.setattr(notes, "update_transcription", update_transcription)
    install(monkeypatch, Calls(RuntimeError("bug")))
    workflow = FileTranscriptionWorkflow(Keys(), notes, Clients())
    with pytest.raises(RuntimeError):
        await workflow.transcribe_pending(
            "n1", RECORDING, CHOICE, OPTIONS, on_progress=no_progress, on_update=no_progress
        )
    assert marked == []
