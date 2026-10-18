"""One microphone recording from the first check to the saved note.

The window owns the screen; this module owns the order of events: check the tools,
open the microphone, stream and capture, save, then hand the audio to whichever
transcription path the chosen model uses.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from kikoe.core.choice import ModelChoice
from kikoe.core.models import Note, Recording, TranscriptionState
from kikoe.core.recording import CaptureProgress, CaptureState
from kikoe.core.transcription import LiveSnapshot
from kikoe.media.ffmpeg import find_ffmpeg
from kikoe.media.recorder import Recorder, RecorderError
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.providers.factory import create_client
from kikoe.services.file_transcription import ApiKeys, ClientFactory
from kikoe.services.realtime import RealtimeRecording
from kikoe.services.recording import RecordingSession
from kikoe.storage.captures import CaptureStore
from kikoe.storage.notes import NoteRepository

log = logging.getLogger(__name__)

LIVE_FINISH_SECONDS = 10
"""How long live recognition may keep finalizing after the microphone stops."""

_CLOSING_POLL_SECONDS = 0.1
"""How often finalizing checks whether the window is closing."""


@dataclass(frozen=True, slots=True)
class RecordingCallbacks:
    """What the screen does at each step; the workflow never touches widgets itself."""

    before_start: Callable[[], Awaitable[None]]
    """Awaited first, inside the guarded section, e.g. to let audio playback end."""
    started: Callable[[Note], None]
    """The note exists, the microphone is open and the session is capturing."""
    progress: Callable[[CaptureProgress], None]
    live_update: Callable[[LiveSnapshot], None]
    finalizing: Callable[[], None]
    """The microphone has stopped; live recognition is about to drain pending text."""
    capture_ended: Callable[[], None]
    """Capture and live recognition have stopped; the audio is about to be saved."""
    saved: Callable[[Note], None]
    nothing_recorded: Callable[[], None]
    capture_failed: Callable[[RecorderError], None]
    """The microphone failed; the audio captured so far is still saved."""
    live_failed: Callable[[Exception], None]
    """Live recognition gave up (a failure reconnecting cannot fix, or a bug); capture goes on."""
    upload: Callable[[str, Recording], Awaitable[None]]
    """Transcribe a saved recording with the batch model."""
    finished: Callable[[], None]
    """Always called last, whether the recording succeeded, failed or was cancelled."""


class RecordingWorkflow:
    """Records once; create a new instance for every recording."""

    def __init__(
        self,
        *,
        notes: NoteRepository,
        keys: ApiKeys,
        callbacks: RecordingCallbacks,
        make_recorder: Callable[[], Recorder],
        open_store: Callable[[], CaptureStore],
        is_closing: Callable[[], bool],
        check_ffmpeg: Callable[[], object] = find_ffmpeg,
        create: ClientFactory = create_client,
    ) -> None:
        self._notes = notes
        self._keys = keys
        self._callbacks = callbacks
        self._make_recorder = make_recorder
        self._open_store = open_store
        self._is_closing = is_closing
        self._check_ffmpeg = check_ffmpeg
        self._create = create
        self.session: RecordingSession | None = None
        """Set as soon as it exists, so a shutdown can stop a microphone that is still opening."""
        self.live: RealtimeRecording | None = None

    async def run(
        self, note_id: str | None, title: str, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        """Record into ``note_id``, or into a new note called ``title``."""
        live_task: asyncio.Task[None] | None = None
        try:
            await self._callbacks.before_start()
            session = await self._open(note_id, title, choice, options)
            note = await session.start(note_id, title, choice)
            self._callbacks.started(note)
            self._callbacks.progress(CaptureProgress(CaptureState.RECORDING, session.elapsed, 0))
            if self._is_closing():
                await session.stop()
            if self.live is not None:
                live_task = asyncio.create_task(self._run_live(self.live))
            error = await session.capture(self._callbacks.progress)
            if live_task is not None:
                self._callbacks.finalizing()
                await self._finish_live(live_task)
            self._callbacks.capture_ended()
            await self._save(session, error)
            if error is not None:
                log.warning("Microphone capture stopped: %s", error)
                self._callbacks.capture_failed(error)
        finally:
            await self._clean_up(live_task)

    async def _open(
        self, note_id: str | None, title: str, choice: ModelChoice, options: TranscriptionOptions
    ) -> RecordingSession:
        """Check everything that can fail before the microphone opens, then build the session."""
        await asyncio.to_thread(self._check_ffmpeg)
        realtime = choice.model.capabilities.realtime is not None
        # Validate the batch backend before opening the microphone.
        api_key = await self._keys.get_api_key(choice.provider.provider_id) if realtime else ""
        client = self._create(choice.provider, api_key)
        store = await asyncio.to_thread(self._open_store)
        session = RecordingSession(self._make_recorder(), store, self._notes)
        self.session = session
        if realtime:
            self.live = RealtimeRecording(
                session, choice, client, options, self._callbacks.live_update
            )
        return session

    async def _run_live(self, live: RealtimeRecording) -> None:
        try:
            await live.run()
        except TranscriptionError as error:
            log.warning("Live recognition stopped: %s", error)
            self._callbacks.live_failed(error)
        except Exception as error:
            log.exception("Live recognition failed")
            self._callbacks.live_failed(error)

    async def _finish_live(self, live_task: asyncio.Task[None]) -> None:
        """Give live recognition time to finalize, unless the window is closing.

        Closing is polled while waiting so a stalled provider cannot hold the window open.
        """
        deadline = asyncio.get_running_loop().time() + LIVE_FINISH_SECONDS
        while not live_task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if self._is_closing() or remaining <= 0:
                live_task.cancel()
                break
            await asyncio.wait({live_task}, timeout=min(remaining, _CLOSING_POLL_SECONDS))
        await asyncio.gather(live_task, return_exceptions=True)

    async def _save(self, session: RecordingSession, error: RecorderError | None) -> None:
        saved = await session.save()
        journal = session.journal
        recording_id = journal.recording_id if journal is not None else ""
        if saved is None:
            if error is None:
                self._callbacks.nothing_recorded()
            return
        self._callbacks.saved(saved)
        if self.live is not None:
            state = (
                TranscriptionState.READY
                if self.live.complete and error is None
                else TranscriptionState.FAILED
            )
            saved = await self._notes.update_transcription(
                saved.note_id, recording_id, state, segments=self.live.segments
            )
            self._callbacks.saved(saved)
        elif error is None and not self._is_closing():
            recording = next(r for r in saved.recordings if r.recording_id == recording_id)
            await self._callbacks.upload(saved.note_id, recording)

    async def _clean_up(self, live_task: asyncio.Task[None] | None) -> None:
        if live_task is not None and not live_task.done():
            live_task.cancel()
            await asyncio.gather(live_task, return_exceptions=True)
        self.live = None
        try:
            if self.session is not None:
                await self.session.stop()
        finally:
            self.session = None
            self._callbacks.finished()
