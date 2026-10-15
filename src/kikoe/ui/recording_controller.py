"""Microphone recording on screen: start, pause, resume, stop and recover."""

import asyncio
from datetime import UTC, datetime

from gi.repository import Adw, Gio, GLib

from kikoe.core.choice import ModelChoice
from kikoe.core.models import Note, Recording
from kikoe.core.recording import CaptureProgress, CaptureState
from kikoe.i18n import _
from kikoe.media.recorder import SoundDeviceRecorder
from kikoe.providers.base import TranscriptionOptions
from kikoe.services.activity import ActivityTracker
from kikoe.services.errors import USER_FACING_ERRORS
from kikoe.services.file_transcription import ClientFactory
from kikoe.services.playback import Playback
from kikoe.services.realtime import RealtimeRecording
from kikoe.services.recording import RecordingSession
from kikoe.services.recording_workflow import RecordingCallbacks, RecordingWorkflow
from kikoe.services.recovery import recover_all
from kikoe.storage.captures import CaptureStore, capture_dir
from kikoe.storage.notes import NoteRepository
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.bottom_bar import BottomBar
from kikoe.ui.labels import capture_label, recording_note_title
from kikoe.ui.note_sidebar import NoteSidebar
from kikoe.ui.note_view import NoteView
from kikoe.ui.recovery_prompt import confirm_recovery
from kikoe.ui.tasks import run_task, show_error
from kikoe.ui.transcription_controller import TranscriptionController
from kikoe.ui.window_host import WindowHost


class RecordingController:
    """Shows a recording's progress and forwards the user's Start, Pause and Stop."""

    def __init__(
        self,
        window: Adw.ApplicationWindow,
        host: WindowHost,
        *,
        providers: ProviderRepository,
        preferences: PreferencesRepository,
        notes: NoteRepository,
        work: ActivityTracker,
        bottom_bar: BottomBar,
        playback: Playback,
        sidebar: NoteSidebar,
        note_view: NoteView,
        transcribe: TranscriptionController,
        clients: ClientFactory,
    ) -> None:
        self._clients = clients
        self._window = window
        self._host = host
        self._providers = providers
        self._preferences = preferences
        self._notes = notes
        self._work = work
        self._bottom_bar = bottom_bar
        self._playback = playback
        self._sidebar = sidebar
        self._note_view = note_view
        self._transcribe = transcribe
        self._workflow: RecordingWorkflow | None = None
        self.note_id: str | None = None
        """The note being recorded into; its sidebar row shows the recording state."""
        self.summary = ""
        self.model = ""

    @property
    def session(self) -> RecordingSession | None:
        return self._workflow.session if self._workflow is not None else None

    @property
    def live(self) -> RealtimeRecording | None:
        return self._workflow.live if self._workflow is not None else None

    def on_start(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        choice = self._bottom_bar.choice
        if (
            choice is None
            or self._work.recording_task is not None
            or self._work.import_task is not None
        ):
            return
        options = self._transcribe.transcription_options(self._bottom_bar.requested_features)
        note = self._host.note
        note_id = note.note_id if note else None
        self._work.recording_task = run_task(self._record(note_id, choice, options), self._window)
        self._host.refresh_actions()

    def on_pause(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self.session is not None:
            run_task(self._change_capture_state(pause=True), self._window)

    def on_resume(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self.session is not None:
            run_task(self._change_capture_state(pause=False), self._window)

    def on_stop(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self.session is not None:
            run_task(self.session.stop(), self._window)

    def show_in_progress(self) -> None:
        """Bring back the recording section after the shown note was replaced."""
        session = self.session
        if session is None or session.state is CaptureState.STOPPED:
            return
        live = self.live
        self._note_view.show_recording(self.summary, self.model, realtime=live is not None)
        if live is not None:
            self._note_view.update_live(live.snapshot())
        self._note_view.recording_view.set_state(session.state)
        self._host.set_subtitle(capture_label(session.state))

    async def stop(self) -> None:
        """Stop the microphone if it is open; the recording is saved by its own task."""
        if self.session is not None:
            await self.session.stop()

    async def _change_capture_state(self, *, pause: bool) -> None:
        session = self.session
        if session is None:
            return
        if pause:
            await session.pause()
        else:
            await session.resume()
        self._refresh_sidebar()

    async def _record(
        self, note_id: str | None, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        async def upload(saved_note_id: str, recording: Recording) -> None:
            await self._transcribe.upload_recording(saved_note_id, recording, choice, options)

        self._workflow = RecordingWorkflow(
            notes=self._notes,
            keys=self._providers,
            create=self._clients,
            callbacks=RecordingCallbacks(
                before_start=self._playback.finish,
                started=lambda note: self._on_started(note, choice, options),
                progress=self._on_progress,
                live_update=self._note_view.update_live,
                capture_ended=self._on_capture_ended,
                saved=self._host.show_note,
                nothing_recorded=lambda: self._host.toast(_("No audio was recorded")),
                capture_failed=lambda error: show_error(self._window, str(error)),
                live_failed=self._on_live_failed,
                upload=upload,
                finished=self._on_finished,
            ),
            make_recorder=lambda: SoundDeviceRecorder(self._preferences.current.input_device),
            open_store=lambda: CaptureStore(capture_dir()),
            is_closing=lambda: self._work.closing,
        )
        await self._workflow.run(note_id, recording_note_title(datetime.now(UTC)), choice, options)

    def _on_started(self, note: Note, choice: ModelChoice, options: TranscriptionOptions) -> None:
        self.note_id = note.note_id
        self.model = choice.model.display_name
        labels: list[str] = []
        if options.timestamps:
            labels.append(_("Timestamps"))
        if options.diarization:
            labels.append(_("Speakers"))
        self.summary = _("{provider} {model} · {options}").format(
            provider=choice.provider.name,
            model=choice.model.display_name,
            options=_("Text only") if not labels else ", ".join(labels),
        )
        self._host.show_note(note)
        self._refresh_sidebar()
        self._host.show_content_pane()
        self._bottom_bar.show_recording(self.summary)

    def _on_capture_ended(self) -> None:
        self._note_view.hide_recording()
        self._bottom_bar.set_progress(_("Saving recording…"))
        self._host.refresh_actions()

    def _on_live_failed(self, error: Exception) -> None:
        if isinstance(error, USER_FACING_ERRORS):
            show_error(self._window, str(error))
        else:
            show_error(
                self._window, _("An unexpected error occurred. See the local log for details.")
            )

    def _on_finished(self) -> None:
        self._workflow = None
        self.note_id = None
        self._work.recording_task = None
        self._note_view.hide_recording()
        self._bottom_bar.set_progress(None)
        self._refresh_sidebar()
        self._host.show_note(self._host.note)

    def _on_progress(self, progress: CaptureProgress) -> None:
        self._bottom_bar.update_recording(progress)
        self._note_view.recording_view.set_state(progress.state)
        if progress.state is not CaptureState.STOPPED:
            self._host.set_subtitle(capture_label(progress.state))
        self._host.refresh_actions()

    def _refresh_sidebar(self) -> None:
        note = self._host.note
        self._sidebar.refresh()
        self._sidebar.select(note.note_id if note else None)

    # Recovery of recordings that an earlier session left unfinished

    async def offer_recovery(self) -> None:
        store = CaptureStore(await asyncio.to_thread(capture_dir))
        journals = await asyncio.to_thread(store.pending)
        if not journals:
            return
        if not await confirm_recovery(self._window):
            return
        self._work.recovery_busy = True
        self._bottom_bar.set_progress(_("Recovering recordings…"))
        self._host.refresh_actions()
        try:
            await recover_all(journals, store, self._notes, self._show_recovered_note)
        finally:
            self._work.recovery_busy = False
            self._bottom_bar.set_progress(None)
            self._host.refresh_actions()

    def _show_recovered_note(self, note: Note) -> None:
        self._host.show_note(note)
        self._sidebar.select(note.note_id)
        self._host.show_content_pane()
