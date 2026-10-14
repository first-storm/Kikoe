"""A recording reaches the screen in a fixed order and always ends with `finished`."""

import asyncio
import struct
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from pathlib import Path
from typing import cast

import pytest

from helpers import LIVE_ONLY, file_caps, run_async
from kikoe.core.choice import ModelChoice
from kikoe.core.models import (
    ApiKind,
    ModelSpec,
    Note,
    Provider,
    Recording,
    Segment,
    TranscriptionState,
)
from kikoe.core.recording import CaptureProgress
from kikoe.core.transcription import LiveSnapshot, LiveState, RealtimeProfile, TranscriptEvent
from kikoe.core.upload import UploadLimits
from kikoe.media import transcode
from kikoe.media.ffmpeg import FFmpegNotFoundError
from kikoe.media.recorder import RecorderError
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionOptions,
    TranscriptionProvider,
)
from kikoe.services import realtime
from kikoe.services import recording_workflow as workflow_module
from kikoe.services.recording_workflow import RecordingCallbacks, RecordingWorkflow
from kikoe.storage.captures import CaptureStore
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository

FILE_MODEL = ModelSpec("whisper-1", "Whisper", file_caps(timestamps=True))
LIVE_MODEL = ModelSpec("live", "Live", LIVE_ONLY)
PROVIDER = Provider("p", "Test", ApiKind.OPENAI, "https://example.test/v1", (FILE_MODEL,))
FILE_CHOICE = ModelChoice(PROVIDER, FILE_MODEL)
LIVE_CHOICE = ModelChoice(PROVIDER, LIVE_MODEL)
OPTIONS = TranscriptionOptions(timestamps=False, diarization=False)
SECOND = struct.pack("<h", 1000) * 16000


class ScriptedRecorder:
    """Yields one second of audio, then waits for stop or fails like a lost device."""

    sample_rate = 16000

    def __init__(self, *, chunks: int = 1, fail_start: bool = False, fail_after: bool = False):
        self.chunk_count = chunks
        self.fail_start = fail_start
        self.fail_after = fail_after
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        if self.fail_start:
            raise RecorderError("No microphone")

    async def stop(self) -> None:
        self._stopped.set()

    async def pause(self) -> None:
        pass

    async def resume(self) -> None:
        pass

    async def chunks(self) -> AsyncIterator[bytes]:
        for _ in range(self.chunk_count):
            yield SECOND
        if self.fail_after:
            raise RecorderError("Microphone disconnected")
        await self._stopped.wait()


class Keys:
    async def get_api_key(self, provider_id: str) -> str:
        return "key"


class BatchClient:
    """Stands in for a file-only service; the workflow only builds it and never calls it."""


class LiveSession:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[TranscriptEvent] = asyncio.Queue()
        self.processed = asyncio.Event()
        self.count = 0

    async def send(self, pcm: bytes) -> None:
        self.count += 1
        self.processed.clear()
        await self.queue.put(
            TranscriptEvent(str(self.count), (Segment(f"Part {self.count}"),), True)
        )
        await self.processed.wait()

    async def flush(self) -> None:
        pass

    async def keep_alive(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            yield await self.queue.get()
            self.processed.set()


class LiveClient:
    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        return RealtimeProfile()

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        return UploadLimits()

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        raise AssertionError("A live recording is not uploaded again")

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        return LiveSession()


class IdentityConverter:
    """Replaces the ffmpeg resampler so live tests need no ffmpeg."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    @classmethod
    async def start(cls, input_rate: int, output_rate: int) -> IdentityConverter:
        return cls()

    async def feed(self, block: bytes) -> None:
        for offset in range(0, len(block), 3200):
            await self.queue.put(block[offset : offset + 3200])

    async def finish(self) -> None:
        await self.queue.put(None)

    async def chunks(self) -> AsyncIterator[bytes]:
        while (block := await self.queue.get()) is not None:
            yield block

    async def close(self) -> None:
        pass


class Screen:
    """Records what the workflow asked the window to do."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.saved: list[Note] = []
        self.uploads: list[tuple[str, Recording]] = []
        self.failures: list[RecorderError] = []
        self.live_failures: list[Exception] = []
        self.progress: list[CaptureProgress] = []
        self.started = asyncio.Event()

    def callbacks(self) -> RecordingCallbacks:
        return RecordingCallbacks(
            before_start=self._before_start,
            started=self._started,
            progress=self.progress.append,
            live_update=lambda _snapshot: None,
            capture_ended=lambda: self.events.append("capture_ended"),
            saved=self._saved,
            nothing_recorded=lambda: self.events.append("nothing_recorded"),
            capture_failed=self._capture_failed,
            live_failed=self.live_failures.append,
            upload=self._upload,
            finished=lambda: self.events.append("finished"),
        )

    async def _before_start(self) -> None:
        self.events.append("before_start")

    def _started(self, note: Note) -> None:
        self.events.append("started")
        self.started.set()

    def _saved(self, note: Note) -> None:
        self.events.append("saved")
        self.saved.append(note)

    def _capture_failed(self, error: RecorderError) -> None:
        self.events.append("capture_failed")
        self.failures.append(error)

    async def _upload(self, note_id: str, recording: Recording) -> None:
        self.events.append("upload")
        self.uploads.append((note_id, recording))


@pytest.fixture
def notes(tmp_path: Path) -> NoteRepository:
    return NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))


@pytest.fixture(autouse=True)
def fake_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    async def to_flac(source: Path, destination: Path, sample_rate: int) -> None:
        data = await asyncio.to_thread(source.read_bytes)
        await asyncio.to_thread(destination.write_bytes, data)

    monkeypatch.setattr(transcode, "pcm_to_flac", to_flac)


def make_workflow(
    notes: NoteRepository,
    tmp_path: Path,
    screen: Screen,
    recorder: ScriptedRecorder | None = None,
    *,
    client: TranscriptionProvider | None = None,
    closing: bool = False,
    check_ffmpeg: Callable[[], object] | None = None,
    make_recorder: Callable[[], ScriptedRecorder] | None = None,
) -> RecordingWorkflow:
    return RecordingWorkflow(
        notes=notes,
        keys=Keys(),
        callbacks=screen.callbacks(),
        make_recorder=make_recorder or (lambda: recorder or ScriptedRecorder()),
        open_store=lambda: CaptureStore(tmp_path / "pending"),
        is_closing=lambda: closing,
        check_ffmpeg=check_ffmpeg or (lambda: None),
        create=lambda _provider, _key: client or cast(TranscriptionProvider, BatchClient()),
    )


async def record_and_stop(
    notes: NoteRepository, workflow: RecordingWorkflow, screen: Screen, choice: ModelChoice
) -> None:
    """Start recording, let the first second be captured, then stop the microphone."""
    await notes.load()
    task = asyncio.create_task(workflow.run(None, "Memo", choice, OPTIONS))
    async with asyncio.timeout(10):
        await screen.started.wait()
        assert workflow.session is not None
        await workflow.session.stop()
        await task


@run_async
async def test_batch_recording_is_saved_then_handed_to_the_upload_callback(
    notes: NoteRepository, tmp_path: Path
) -> None:
    screen = Screen()
    workflow = make_workflow(notes, tmp_path, screen)
    await record_and_stop(notes, workflow, screen, FILE_CHOICE)
    assert screen.events == [
        "before_start",
        "started",
        "capture_ended",
        "saved",
        "upload",
        "finished",
    ]
    note_id, recording = screen.uploads[0]
    assert note_id == screen.saved[0].note_id
    assert recording.transcription_state is TranscriptionState.PENDING
    assert recording.duration == timedelta(seconds=1)
    assert screen.progress[0].elapsed == timedelta()
    assert workflow.session is None
    assert workflow.live is None


@run_async
async def test_missing_microphone_creates_no_note_and_still_finishes(
    notes: NoteRepository, tmp_path: Path
) -> None:
    screen = Screen()
    workflow = make_workflow(notes, tmp_path, screen, ScriptedRecorder(fail_start=True))
    await notes.load()
    with pytest.raises(RecorderError, match="No microphone"):
        await workflow.run(None, "Memo", FILE_CHOICE, OPTIONS)
    assert screen.events == ["before_start", "finished"]
    assert notes.notes == ()


@run_async
async def test_missing_ffmpeg_stops_before_the_microphone_opens(
    notes: NoteRepository, tmp_path: Path
) -> None:
    screen = Screen()
    opened: list[str] = []

    def missing() -> None:
        raise FFmpegNotFoundError("ffmpeg is not installed")

    def microphone() -> ScriptedRecorder:
        opened.append("microphone")
        return ScriptedRecorder()

    workflow = make_workflow(
        notes, tmp_path, screen, check_ffmpeg=missing, make_recorder=microphone
    )
    with pytest.raises(FFmpegNotFoundError):
        await workflow.run(None, "Memo", FILE_CHOICE, OPTIONS)
    assert opened == []
    assert screen.events == ["before_start", "finished"]


@run_async
async def test_device_failure_keeps_the_audio_and_reports_without_uploading(
    notes: NoteRepository, tmp_path: Path
) -> None:
    screen = Screen()
    workflow = make_workflow(notes, tmp_path, screen, ScriptedRecorder(fail_after=True))
    await notes.load()
    await workflow.run(None, "Memo", FILE_CHOICE, OPTIONS)
    assert screen.events == [
        "before_start",
        "started",
        "capture_ended",
        "saved",
        "capture_failed",
        "finished",
    ]
    assert screen.saved[0].recordings[0].duration == timedelta(seconds=1)
    assert screen.uploads == []
    assert str(screen.failures[0]) == "Microphone disconnected"


@run_async
async def test_a_recording_with_no_audio_is_reported_and_not_uploaded(
    notes: NoteRepository, tmp_path: Path
) -> None:
    screen = Screen()
    workflow = make_workflow(notes, tmp_path, screen, ScriptedRecorder(chunks=0))
    await record_and_stop(notes, workflow, screen, FILE_CHOICE)
    assert screen.events == [
        "before_start",
        "started",
        "capture_ended",
        "nothing_recorded",
        "finished",
    ]
    assert screen.uploads == []


@run_async
async def test_closing_the_window_saves_the_audio_but_skips_the_upload(
    notes: NoteRepository, tmp_path: Path
) -> None:
    screen = Screen()
    workflow = make_workflow(notes, tmp_path, screen, closing=True)
    await notes.load()
    await asyncio.wait_for(workflow.run(None, "Memo", FILE_CHOICE, OPTIONS), 10)
    assert screen.events == ["before_start", "started", "capture_ended", "saved", "finished"]
    assert screen.saved[0].recordings[0].transcription_state is TranscriptionState.PENDING


@run_async
async def test_live_recording_stores_final_text_and_is_not_uploaded(
    notes: NoteRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "PCMStream", IdentityConverter)
    screen = Screen()
    workflow = make_workflow(notes, tmp_path, screen, client=LiveClient())
    await record_and_stop(notes, workflow, screen, LIVE_CHOICE)
    assert screen.events == [
        "before_start",
        "started",
        "capture_ended",
        "saved",
        "saved",
        "finished",
    ]
    final = screen.saved[-1].recordings[0]
    assert final.transcription_state is TranscriptionState.READY
    texts = [s.text for s in final.segments]
    assert texts and texts == [f"Part {n}" for n in range(1, len(texts) + 1)]
    assert screen.uploads == []


class ExplodingLive:
    """A live job whose recognition fails with something nobody handled."""

    complete = False
    segments: tuple[Segment, ...] = ()

    def __init__(self, *_args: object) -> None:
        pass

    async def run(self) -> None:
        raise RuntimeError("recognizer bug")

    def snapshot(self) -> LiveSnapshot:
        return LiveSnapshot((), (), LiveState.CONNECTING)


@run_async
async def test_unhandled_live_failure_is_reported_and_the_recording_is_marked_failed(
    notes: NoteRepository, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workflow_module, "RealtimeRecording", ExplodingLive)
    screen = Screen()
    workflow = make_workflow(notes, tmp_path, screen, client=LiveClient())
    await record_and_stop(notes, workflow, screen, LIVE_CHOICE)
    assert [str(e) for e in screen.live_failures] == ["recognizer bug"]
    assert screen.saved[-1].recordings[0].transcription_state is TranscriptionState.FAILED
    assert screen.events[-1] == "finished"
