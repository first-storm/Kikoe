"""Disk-backed streaming remains recoverable when the SDK disconnects or stalls."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import LIVE_ONLY, run_async
from kikoe.core.choice import ModelChoice
from kikoe.core.models import ApiKind, ModelSpec, Provider, Segment
from kikoe.core.recording import CaptureState
from kikoe.core.transcription import LiveSnapshot, LiveState, TranscriptEvent
from kikoe.core.upload import UploadLimits
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
)
from kikoe.services import realtime
from kikoe.services.realtime import RealtimeRecording
from kikoe.services.recording import RecordingSession
from kikoe.storage.captures import CaptureJournal, CaptureStore
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository

MODEL = ModelSpec("gemini-3.5-transcribe-live", "Gemini Live", LIVE_ONLY)
CHOICE = ModelChoice(Provider("p", "Gemini", ApiKind.GEMINI, "https://example.test"), MODEL)
PCM = bytes([1, 0]) * 3200


class Recorder:
    sample_rate = 16000

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def pause(self) -> None:
        pass

    async def resume(self) -> None:
        pass

    async def chunks(self) -> AsyncIterator[bytes]:
        yield PCM


class Converter:
    """A bounded identity converter avoids depending on ffmpeg in workflow tests."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.closed = False

    @classmethod
    async def start(cls, input_rate: int, output_rate: int) -> Converter:
        assert input_rate == output_rate == 16000
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
        self.closed = True


class Session:
    def __init__(self, fail: bool = False, *, hang: bool = False) -> None:
        self.fail = fail
        self.hang = hang
        self.audio = bytearray()
        self.queue: asyncio.Queue[TranscriptEvent] = asyncio.Queue()
        self.processed = asyncio.Event()
        self.closed = False
        self.count = 0
        self.closed_event = asyncio.Event()

    async def send(self, pcm: bytes) -> None:
        if self.fail and self.count:
            raise ConnectionError("Disconnected")
        self.audio.extend(pcm)
        self.count += 1
        self.processed.clear()
        text = "Old recognition" if self.fail else f"Part {self.count}"
        await self.queue.put(TranscriptEvent(str(self.count), (Segment(text),), True))
        await self.processed.wait()

    async def flush(self) -> None:
        if self.hang:
            await asyncio.Event().wait()

    async def keep_alive(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        self.closed_event.set()

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            yield await self.queue.get()
            self.processed.set()


class Client:
    max_upload_bytes: int | None = None

    def __init__(
        self, *, reconnect: bool = False, hang: bool = False, reject: bool = False
    ) -> None:
        self.reconnect = reconnect
        self.hang = hang
        self.reject = reject
        self.sessions: list[Session] = []
        self.connected = asyncio.Event()

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
        raise AssertionError("Live success must not upload the file again")

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        if self.reject:
            raise TranscriptionError("Bad key")
        session = Session(self.reconnect and not self.sessions, hang=self.hang)
        self.sessions.append(session)
        if len(self.sessions) > 1 or not self.reconnect:
            self.connected.set()
        return session


def capture(tmp_path: Path, *, finished: bool) -> RecordingSession:
    session = RecordingSession(
        Recorder(),
        CaptureStore(tmp_path / "captures"),
        NoteRepository(Library(tmp_path / "library", tmp_path / "audio")),
    )
    session.journal = CaptureJournal(
        "a" * 32, "note", "Test", datetime.now(UTC), 16000, "Gemini", MODEL.model_id
    )
    session.store.create(session.journal)
    session.store.append(session.journal, PCM)
    session.elapsed = timedelta(milliseconds=200)
    session.finished = finished
    session.state = CaptureState.STOPPED if finished else CaptureState.RECORDING
    return session


@run_async
async def test_live_success_keeps_final_text_and_original_pcm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "PCMStream", Converter)
    recorded = capture(tmp_path, finished=True)
    client = Client()
    snapshots: list[LiveSnapshot] = []
    job = RealtimeRecording(
        recorded, CHOICE, client, TranscriptionOptions(False, False), snapshots.append
    )
    await job.run()
    assert job.complete
    assert job.segments == (Segment("Part 1"), Segment("Part 2"))
    assert recorded.journal is not None
    assert recorded.store.transcript(recorded.journal) == job.segments
    assert recorded.store.pcm_path(recorded.journal).read_bytes() == PCM
    assert client.sessions[0].closed


@run_async
async def test_reconnection_replays_disk_audio_and_replaces_old_recognition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "PCMStream", Converter)
    recorded = capture(tmp_path, finished=False)
    client = Client(reconnect=True)
    snapshots: list[LiveSnapshot] = []
    job = RealtimeRecording(
        recorded, CHOICE, client, TranscriptionOptions(False, False), snapshots.append
    )
    task = asyncio.create_task(job.run())
    async with asyncio.timeout(5):
        await client.connected.wait()
        recorded.finished = True
        recorded.stopped.set()
        recorded.audio_changed.set()
        await task
    assert job.complete
    assert job.segments == (Segment("Part 1"), Segment("Part 2"))
    assert bytes(client.sessions[1].audio) == PCM
    assert all(s.closed for s in client.sessions)
    assert any(s.state is LiveState.RECONNECTING for s in snapshots)


@run_async
async def test_rejected_credentials_leave_recording_available_without_retry_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "PCMStream", Converter)
    recorded = capture(tmp_path, finished=False)
    snapshots: list[LiveSnapshot] = []
    job = RealtimeRecording(
        recorded, CHOICE, Client(reject=True), TranscriptionOptions(), snapshots.append
    )
    await job.run()
    assert not job.complete
    assert snapshots[-1].state is LiveState.INCOMPLETE
    assert recorded.journal is not None
    assert recorded.store.pcm_path(recorded.journal).read_bytes() == PCM


@run_async
async def test_cancelling_stalled_final_flush_preserves_confirmed_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "PCMStream", Converter)
    recorded = capture(tmp_path, finished=True)
    client = Client(hang=True)
    received = asyncio.Event()

    def update(snapshot: LiveSnapshot) -> None:
        if len(snapshot.segments) == 2:
            received.set()

    job = RealtimeRecording(recorded, CHOICE, client, TranscriptionOptions(False, False), update)
    task = asyncio.create_task(job.run())
    async with asyncio.timeout(3):
        await received.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not job.complete
    assert recorded.journal is not None
    assert recorded.store.transcript(recorded.journal) == job.segments
    assert len(job.segments) == 2
    assert client.sessions[0].closed


@run_async
async def test_pause_flushes_and_resume_reads_only_new_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "PCMStream", Converter)
    recorded = capture(tmp_path, finished=False)
    client = Client()
    job = RealtimeRecording(
        recorded, CHOICE, client, TranscriptionOptions(False, False), lambda _s: None
    )
    task = asyncio.create_task(job.run())
    async with asyncio.timeout(5):
        await client.connected.wait()
        recorded.state = CaptureState.PAUSED
        recorded.audio_changed.set()
        await client.sessions[0].closed_event.wait()
        assert not job.complete
        assert recorded.journal is not None
        recorded.store.append(recorded.journal, PCM)
        recorded.elapsed = timedelta(milliseconds=400)
        recorded.state = CaptureState.STOPPED
        recorded.finished = True
        recorded.stopped.set()
        recorded.state_changed.set()
        recorded.audio_changed.set()
        await task
    assert job.complete
    assert len(client.sessions) == 2
    assert bytes(client.sessions[0].audio) == PCM
    assert bytes(client.sessions[1].audio) == PCM
    assert len(job.segments) == 4


@run_async
async def test_gemini_rotation_does_not_mark_unstreamed_tail_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(realtime, "PCMStream", Converter)
    monkeypatch.setattr(realtime, "GEMINI_SESSION_SECONDS", 0)
    recorded = capture(tmp_path, finished=True)
    client = Client()
    job = RealtimeRecording(
        recorded, CHOICE, client, TranscriptionOptions(False, False), lambda _s: None
    )
    async with asyncio.timeout(5):
        await job.run()
    assert job.complete
    assert b"".join(bytes(s.audio) for s in client.sessions) == PCM
    assert len(client.sessions) == 2
    assert len(job.segments) == 2
