"""Capture, recover and transcribe audio while retaining it through every upload failure."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kikoe.core.choice import ModelChoice
from kikoe.core.models import Note, Recording, RecordingSource, TranscriptionState
from kikoe.core.recording import CaptureProgress, CaptureState, pcm_level
from kikoe.core.transcription import model_support, scope_speakers
from kikoe.i18n import _
from kikoe.media import transcode
from kikoe.media.recorder import Recorder, RecorderError
from kikoe.providers.base import TranscriptionError, TranscriptionOptions, TranscriptionProvider
from kikoe.services.importer import ImportStage, ProgressCallback, transcribe_audio
from kikoe.storage.captures import CaptureJournal, CaptureStore
from kikoe.storage.library import NoteNotFoundError
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
        journal = self._require_journal()
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
        return await recover_recording(self._require_journal(), self.store, self.notes)

    def _require_journal(self) -> CaptureJournal:
        if self.journal is None:
            raise RuntimeError("Recording has not started")
        return self.journal

    def _notify(self, level: float) -> None:
        if self._on_progress is not None:
            self._on_progress(CaptureProgress(self.state, self.elapsed, level))


async def recover_recording(
    journal: CaptureJournal,
    store: CaptureStore,
    notes: NoteRepository,
) -> Note | None:
    """Recover without uploading; a commit followed by a crash cannot create duplicates."""
    existing = await notes.find_recording_note(journal.recording_id)
    if existing is not None:
        await asyncio.to_thread(store.discard, journal)
        return existing
    duration = await asyncio.to_thread(store.finish, journal)
    if duration <= timedelta():
        await asyncio.to_thread(store.discard, journal)
        return None
    flac = store.flac_path(journal)
    await transcode.pcm_to_flac(store.pcm_path(journal), flac, journal.sample_rate)
    original = await notes.store_audio(flac, journal.recording_id)
    recording = Recording(
        journal.recording_id,
        journal.created_at,
        duration,
        RecordingSource.RECORDED,
        journal.provider_name,
        journal.model_id,
        audio_path=original,
        segments=await asyncio.to_thread(store.transcript, journal),
        transcription_state=TranscriptionState.PENDING,
    )
    try:
        note = await notes.add_recording(journal.note_id, journal.title, recording)
    except NoteNotFoundError:
        note = await notes.add_recording(None, journal.title, recording)
    await asyncio.to_thread(store.discard, journal)
    return note


async def transcribe_recording(
    note_id: str,
    recording: Recording,
    choice: ModelChoice,
    *,
    client: TranscriptionProvider,
    notes: NoteRepository,
    options: TranscriptionOptions,
    on_progress: ProgressCallback,
    work_dir: Path | None = None,
    preserve_on_failure: bool = False,
) -> Note:
    """Update the saved recording; cancellation never removes its original audio."""
    if recording.audio_path is None:
        raise ValueError("A recording needs retained audio before transcription")
    support = model_support(choice.provider.api, choice.model)
    if not support.file:
        raise TranscriptionError(_("Choose a file transcription model to retry this recording."))
    if not preserve_on_failure:
        await notes.update_transcription(
            note_id, recording.recording_id, TranscriptionState.PENDING
        )
    try:
        _duration, segments = await transcribe_audio(
            recording.audio_path,
            replace(choice.model, capabilities=support.batch),
            client=client,
            options=options,
            on_progress=on_progress,
            work_dir=work_dir,
        )
        on_progress(ImportStage.SAVING, 1, 1)
        commit = asyncio.create_task(
            notes.update_transcription(
                note_id,
                recording.recording_id,
                TranscriptionState.READY,
                segments=scope_speakers(segments, recording.recording_id),
                provider_name=choice.provider.name,
                model_id=choice.model.model_id,
            )
        )
        try:
            return await asyncio.shield(commit)
        except asyncio.CancelledError:
            # A worker-thread transaction cannot be cancelled after it starts. Finish
            # it before closing, rather than report cancellation while it writes text.
            return await commit
    except BaseException:
        if not preserve_on_failure:
            await notes.update_transcription(
                note_id, recording.recording_id, TranscriptionState.FAILED
            )
        raise
