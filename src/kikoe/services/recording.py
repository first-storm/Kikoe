"""Own the microphone and its durable journal while a recording is captured."""

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from kikoe.core.choice import ModelChoice
from kikoe.core.models import Note
from kikoe.core.recording import CaptureProgress, CaptureState, pcm_level
from kikoe.media.recorder import Recorder, RecorderError
from kikoe.services.recovery import recover_recording
from kikoe.storage.captures import CaptureJournal, CaptureStore
from kikoe.storage.notes import NoteRepository


class RecordingSession:
    """Own one microphone and its durable journal; the UI only forwards intent."""

    def __init__(self, recorder: Recorder, store: CaptureStore, notes: NoteRepository) -> None:
        self.recorder = recorder
        self.store = store
        self.notes = notes
        self.journal: CaptureJournal | None = None
        self.state = CaptureState.STOPPED
        self.elapsed = timedelta()
        self._frames = 0
        self._on_progress: Callable[[CaptureProgress], None] | None = None
        self.audio_changed = asyncio.Event()
        self.finished = False
        self.stopped = asyncio.Event()
        self.state_changed = asyncio.Event()

    async def start(self, note_id: str | None, title: str, choice: ModelChoice) -> Note:
        """Open first, so a missing microphone does not leave a new empty note."""
        await self.recorder.start()
        created: Note | None = None
        try:
            note = (
                await self.notes.get_note(note_id)
                if note_id
                else await self.notes.create_note(title)
            )
            if note_id is None:
                created = note
            self.journal = CaptureJournal(
                uuid.uuid4().hex,
                note.note_id,
                note.title,
                datetime.now(UTC),
                self.recorder.sample_rate,
                choice.provider.name,
                choice.model.model_id,
            )
            await asyncio.to_thread(self.store.create, self.journal)
            self.state = CaptureState.RECORDING
            return note
        except BaseException:
            await self.recorder.stop()
            if created is not None:
                await self.notes.delete_note(created.note_id)
            raise

    async def capture(self, on_progress: Callable[[CaptureProgress], None]) -> RecorderError | None:
        """Write before notifying the UI; capture errors still leave a recoverable prefix."""
        journal = self.require_journal()
        self._on_progress = on_progress
        capture_error: RecorderError | None = None
        try:
            async for chunk in self.recorder.chunks():
                await asyncio.to_thread(self.store.append, journal, chunk)
                self._frames += len(chunk) // 2
                self.elapsed = timedelta(seconds=self._frames / journal.sample_rate)
                self._notify(pcm_level(chunk) if self.state is CaptureState.RECORDING else 0)
                self.audio_changed.set()
        except RecorderError as error:
            capture_error = error
        finally:
            try:
                await self.recorder.stop()
            except RecorderError as error:
                capture_error = capture_error or error
            self.elapsed = await asyncio.to_thread(self.store.finish, journal)
            self.state = CaptureState.STOPPED
            self.finished = True
            self.stopped.set()
            self.state_changed.set()
            self.audio_changed.set()
            self._notify(0)
        return capture_error

    async def pause(self) -> None:
        if self.state is not CaptureState.RECORDING:
            return
        await self.recorder.pause()
        self.state = CaptureState.PAUSED
        self.state_changed.set()
        self.audio_changed.set()
        self._notify(0)

    async def resume(self) -> None:
        if self.state is not CaptureState.PAUSED:
            return
        await self.recorder.resume()
        self.state = CaptureState.RECORDING
        self.state_changed.set()
        self._notify(0)

    async def stop(self) -> None:
        await self.recorder.stop()

    async def save(self) -> Note | None:
        """Finalize this journal exactly as the startup recovery path would."""
        return await recover_recording(self.require_journal(), self.store, self.notes)

    def require_journal(self) -> CaptureJournal:
        """The started recording's journal; streaming and saving need one."""
        if self.journal is None:
            raise RuntimeError("Recording has not started")
        return self.journal

    def _notify(self, level: float) -> None:
        if self._on_progress is not None:
            self._on_progress(CaptureProgress(self.state, self.elapsed, level))
