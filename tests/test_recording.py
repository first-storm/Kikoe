"""Captured speech stays recoverable independently of a transcription service."""

import asyncio
import struct
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.choice import ModelChoice
from kikoe.core.models import (
    ApiKind,
    Capabilities,
    ModelSpec,
    Provider,
    Segment,
    TranscriptionState,
)
from kikoe.core.recording import CaptureProgress, CaptureState, pcm_level
from kikoe.media import transcode
from kikoe.media.recorder import RecorderError
from kikoe.providers.base import (
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
    UploadLimits,
)
from kikoe.services.recording import RecordingSession, recover_recording, transcribe_recording
from kikoe.storage.captures import CaptureJournal, CaptureStore, CaptureStoreError
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository

MODEL = ModelSpec("whisper-1", "Whisper", Capabilities(timestamps=True))
PROVIDER = Provider("p", "OpenAI", ApiKind.OPENAI, "https://example.test/v1", (MODEL,))
CHOICE = ModelChoice(PROVIDER, MODEL)


class FakeRecorder:
    """Yield exactly the frames a fake device captured, without accessing hardware."""

    sample_rate = 16000

    def __init__(self, *, fail_start: bool = False, fail_capture: bool = False) -> None:
        self.fail_start = fail_start
        self.fail_capture = fail_capture
        self.stopped = False
        self.paused = False
        self.started = asyncio.Event()
        self.continue_capture = asyncio.Event()

    async def start(self) -> None:
        if self.fail_start:
            raise RecorderError("No microphone")
        self.started.set()

    async def pause(self) -> None:
        self.paused = True

    async def resume(self) -> None:
        self.paused = False
        self.continue_capture.set()

    async def stop(self) -> None:
        self.stopped = True
        self.continue_capture.set()

    async def chunks(self) -> AsyncIterator[bytes]:
        yield struct.pack("<h", 1000) * self.sample_rate
        await self.continue_capture.wait()
        if self.fail_capture:
            raise RecorderError("Microphone disconnected")
        if not self.stopped:
            yield struct.pack("<h", 2000) * self.sample_rate


class FakeClient:
    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        return UploadLimits(self.max_upload_bytes)

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        raise TranscriptionError("Not a realtime fake")

    max_upload_bytes: int | None = None

    def __init__(self, fail: bool = False, hang: bool = False, empty: bool = False) -> None:
        self.fail = fail
        self.hang = hang
        self.empty = empty
        self.started = asyncio.Event()

    async def transcribe_file(
        self, audio: Path, model: ModelSpec, options: TranscriptionOptions
    ) -> tuple[Segment, ...]:
        self.started.set()
        if self.fail:
            raise TranscriptionError("Server unavailable")
        if self.hang:
            await asyncio.Event().wait()
        return () if self.empty else (Segment("Hello", timedelta(seconds=0), timedelta(seconds=1)),)


@pytest.fixture
def notes(tmp_path: Path) -> NoteRepository:
    return NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))


@pytest.fixture
def store(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path / "pending")


@pytest.fixture(autouse=True)
def fake_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    async def to_flac(source: Path, destination: Path, sample_rate: int) -> None:
        data = await asyncio.to_thread(source.read_bytes)
        await asyncio.to_thread(destination.write_bytes, data)

    async def to_upload(
        source: Path,
        destination: Path,
        start: timedelta | None = None,
        end: timedelta | None = None,
    ) -> timedelta:
        await asyncio.to_thread(destination.write_bytes, b"upload")
        return timedelta(seconds=2)

    monkeypatch.setattr(transcode, "pcm_to_flac", to_flac)
    monkeypatch.setattr(transcode, "to_upload", to_upload)


def journal(note_id: str = "deleted") -> CaptureJournal:
    return CaptureJournal(
        "a" * 32, note_id, "Recovered", datetime.now(UTC), 16000, "OpenAI", "whisper-1"
    )


def test_pcm_level_handles_silence_and_full_scale() -> None:
    assert pcm_level(b"") == 0
    assert pcm_level(b"\0" * 100) == 0
    assert pcm_level(struct.pack("<h", -32768) * 100) == 1
    assert 0 < pcm_level(struct.pack("<h", 1000) * 100) < 1
    with pytest.raises(ValueError, match="complete"):
        pcm_level(b"x")


def test_journal_reopens_and_repairs_only_an_incomplete_sample(store: CaptureStore) -> None:
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0" * 32000 + b"x")
    reopened = CaptureStore(store.directory)
    assert reopened.pending() == (entry,)
    assert reopened.finish(entry) == timedelta(seconds=1)
    assert reopened.pcm_path(entry).stat().st_size == 32000
    assert "api_key" not in (store.directory / f"{entry.recording_id}.json").read_text()


@pytest.mark.parametrize(
    "content", ['{"version": 2}', '{"version": 1, "recording_id": "../escape"}', "broken"]
)
def test_damaged_journal_is_reported_without_deleting_audio(
    store: CaptureStore, content: str
) -> None:
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0\0")
    metadata = store.directory / f"{entry.recording_id}.json"
    metadata.write_text(content)
    with pytest.raises(CaptureStoreError):
        store.pending()
    assert store.pcm_path(entry).read_bytes() == b"\0\0"
    assert metadata.read_text() == content


@run_async
async def test_missing_microphone_creates_no_note(
    notes: NoteRepository, store: CaptureStore
) -> None:
    await notes.load()
    session = RecordingSession(FakeRecorder(fail_start=True), store, notes)
    with pytest.raises(RecorderError):
        await session.start(None, "Memo", CHOICE)
    assert notes.notes == ()
    assert store.pending() == ()


@run_async
async def test_pause_freezes_time_and_resume_appends_audio(
    notes: NoteRepository, store: CaptureStore
) -> None:
    await notes.load()
    recorder = FakeRecorder()
    session = RecordingSession(recorder, store, notes)
    note = await session.start(None, "Memo", CHOICE)
    progress: list[CaptureProgress] = []
    first_chunk = asyncio.Event()

    def changed(value: CaptureProgress) -> None:
        progress.append(value)
        if value.elapsed == timedelta(seconds=1):
            first_chunk.set()

    task = asyncio.create_task(session.capture(changed))
    await first_chunk.wait()
    await session.pause()
    assert session.elapsed == timedelta(seconds=1)
    assert progress[-1] == CaptureProgress(CaptureState.PAUSED, timedelta(seconds=1), 0)
    await session.resume()
    assert await task is None
    saved = await session.save()
    assert saved is not None and saved.note_id == note.note_id
    assert saved.recordings[0].duration == timedelta(seconds=2)
    assert saved.recordings[0].transcription_state is TranscriptionState.PENDING
    assert saved.recordings[0].audio_path is not None
    assert saved.recordings[0].audio_path.stat().st_size == 64000
    assert store.pending() == ()


@run_async
async def test_device_failure_keeps_the_captured_prefix(
    notes: NoteRepository, store: CaptureStore
) -> None:
    await notes.load()
    recorder = FakeRecorder(fail_capture=True)
    session = RecordingSession(recorder, store, notes)
    await session.start(None, "Memo", CHOICE)
    recorder.continue_capture.set()
    error = await session.capture(lambda _progress: None)
    assert isinstance(error, RecorderError)
    saved = await session.save()
    assert saved is not None and saved.duration == timedelta(seconds=1)


@run_async
async def test_recovery_appends_to_existing_note_and_never_uploads(
    notes: NoteRepository, store: CaptureStore
) -> None:
    await notes.load()
    note = await notes.create_note("Meeting")
    entry = journal(note.note_id)
    store.create(entry)
    store.append(entry, b"\0" * 32000)
    recovered = await recover_recording(entry, store, notes)
    assert recovered is not None and recovered.note_id == note.note_id
    assert recovered.title == "Meeting"
    assert recovered.recordings[0].transcription_state is TranscriptionState.PENDING


@run_async
async def test_recovery_after_database_commit_is_idempotent(
    notes: NoteRepository, store: CaptureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await notes.load()
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0" * 32000)
    real_discard = store.discard

    def interrupted(_entry: CaptureJournal) -> None:
        raise CaptureStoreError("Simulated crash after commit")

    monkeypatch.setattr(store, "discard", interrupted)
    with pytest.raises(CaptureStoreError):
        await recover_recording(entry, store, notes)
    monkeypatch.setattr(store, "discard", real_discard)
    recovered = await recover_recording(entry, store, notes)
    assert recovered is not None and recovered.title == "Recovered"
    assert len(notes.notes) == 1
    assert len(recovered.recordings) == 1
    assert store.pending() == ()


@run_async
async def test_failed_finalization_keeps_recovery_audio(
    notes: NoteRepository, store: CaptureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    await notes.load()
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0" * 32000)

    async def fail(_source: Path, _destination: Path, _sample_rate: int) -> None:
        raise RecorderError("Failed conversion")

    monkeypatch.setattr(transcode, "pcm_to_flac", fail)
    with pytest.raises(RecorderError):
        await recover_recording(entry, store, notes)
    assert store.pending() == (entry,)
    assert store.pcm_path(entry).stat().st_size == 32000
    assert notes.notes == ()


@run_async
async def test_failure_and_retry_update_the_same_retained_recording(
    notes: NoteRepository, store: CaptureStore, tmp_path: Path
) -> None:
    await notes.load()
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0" * 32000)
    note = await recover_recording(entry, store, notes)
    assert note is not None
    recording = note.recordings[0]
    assert recording.audio_path is not None
    with pytest.raises(TranscriptionError):
        await transcribe_recording(
            note.note_id,
            recording,
            CHOICE,
            client=FakeClient(fail=True),
            notes=notes,
            options=TranscriptionOptions(),
            on_progress=lambda _stage, _part, _parts: None,
            work_dir=tmp_path / "work",
        )
    failed = await notes.get_note(note.note_id)
    assert failed.recordings[0].transcription_state is TranscriptionState.FAILED
    assert recording.audio_path.exists()
    changed = replace(CHOICE, model=replace(MODEL, model_id="replacement"))
    result = await transcribe_recording(
        note.note_id,
        recording,
        changed,
        client=FakeClient(),
        notes=notes,
        options=TranscriptionOptions(),
        on_progress=lambda _stage, _part, _parts: None,
        work_dir=tmp_path / "work",
    )
    assert len(result.recordings) == 1
    assert result.recordings[0].recording_id == recording.recording_id
    assert result.recordings[0].transcription_state is TranscriptionState.READY
    assert result.recordings[0].model_id == "replacement"
    assert result.recordings[0].segments[0].text == "Hello"
    assert list((tmp_path / "work").iterdir()) == []


@run_async
async def test_cancelled_upload_keeps_audio_and_cleans_temporary_files(
    notes: NoteRepository, store: CaptureStore, tmp_path: Path
) -> None:
    await notes.load()
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0" * 32000)
    note = await recover_recording(entry, store, notes)
    assert note is not None
    recording = note.recordings[0]
    client = FakeClient(hang=True)
    task = asyncio.create_task(
        transcribe_recording(
            note.note_id,
            recording,
            CHOICE,
            client=client,
            notes=notes,
            options=TranscriptionOptions(),
            on_progress=lambda _stage, _part, _parts: None,
            work_dir=tmp_path / "work",
        )
    )
    await client.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    saved = await notes.get_note(note.note_id)
    assert saved.recordings[0].transcription_state is TranscriptionState.FAILED
    assert recording.audio_path is not None and recording.audio_path.exists()
    assert list((tmp_path / "work").iterdir()) == []


@run_async
async def test_empty_successful_transcript_is_ready(
    notes: NoteRepository, store: CaptureStore, tmp_path: Path
) -> None:
    await notes.load()
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0" * 32000)
    note = await recover_recording(entry, store, notes)
    assert note is not None
    result = await transcribe_recording(
        note.note_id,
        note.recordings[0],
        CHOICE,
        client=FakeClient(empty=True),
        notes=notes,
        options=TranscriptionOptions(),
        on_progress=lambda _stage, _part, _parts: None,
        work_dir=tmp_path,
    )
    assert result.recordings[0].transcription_state is TranscriptionState.READY
    assert result.recordings[0].segments == ()


@run_async
async def test_recovery_restores_confirmed_live_text_and_removes_sidecar_after_commit(
    store: CaptureStore, notes: NoteRepository
) -> None:
    await notes.load()
    entry = journal()
    store.create(entry)
    store.append(entry, bytes(32000))
    segments = (Segment("Confirmed", timedelta(), timedelta(seconds=1), "recording:0"),)
    store.checkpoint(entry, segments)
    assert store.pending() == (entry,)
    note = await recover_recording(entry, store, notes)
    assert note is not None
    assert note.recordings[0].segments == segments
    assert note.recordings[0].transcription_state is TranscriptionState.PENDING
    assert store.pending() == ()
    assert not list(store.directory.glob("*.transcript"))
    same = await recover_recording(entry, store, notes)
    assert same == note


def test_damaged_live_checkpoint_keeps_pcm_for_recovery(store: CaptureStore) -> None:
    entry = journal()
    store.create(entry)
    store.append(entry, bytes(32000))
    (store.directory / f"{entry.recording_id}.transcript").write_text('{"version": 99}')
    with pytest.raises(CaptureStoreError, match="audio has been kept"):
        store.transcript(entry)
    assert store.pcm_path(entry).read_bytes() == bytes(32000)
