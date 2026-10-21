"""Importing audio and (re)transcribing recordings, with the live preview on screen."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gi.repository import Adw, Gio, GLib

from kikoe.core.activity import Stage
from kikoe.core.choice import ModelChoice, RequestedFeatures, available_models
from kikoe.core.models import Recording, RecordingSource, TranscriptionState
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionOptions
from kikoe.services.activity import ActivityTracker
from kikoe.services.errors import USER_FACING_ERRORS
from kikoe.services.file_transcription import ClientFactory, FileTranscriptionWorkflow
from kikoe.services.importer import ImportStage
from kikoe.services.playback import Playback
from kikoe.storage.notes import NoteRepository
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.bottom_bar import BottomBar
from kikoe.ui.file_dialogs import pick_audio_file
from kikoe.ui.file_transcription import FileTranscriptionDisplay
from kikoe.ui.labels import stage_label
from kikoe.ui.note_view import NoteView
from kikoe.ui.retranscribe_dialog import RetranscribeDialog
from kikoe.ui.speaker_controller import SpeakerController
from kikoe.ui.tasks import run_task, show_error
from kikoe.ui.window_host import WindowHost

log = logging.getLogger(__name__)

_STAGES = {
    ImportStage.CONVERTING: Stage.CONVERTING,
    ImportStage.TRANSCRIBING: Stage.TRANSCRIBING,
    ImportStage.SAVING: Stage.SAVING,
}


class TranscriptionController:
    """Runs file transcriptions for the window and keeps their progress on screen."""

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
        speakers: SpeakerController,
        note_view: NoteView,
        clients: ClientFactory,
    ) -> None:
        self._window = window
        self._host = host
        self._providers = providers
        self._preferences = preferences
        self._work = work
        self._bottom_bar = bottom_bar
        self._playback = playback
        self._speakers = speakers
        self._workflow = FileTranscriptionWorkflow(providers, notes, clients)
        self.display = FileTranscriptionDisplay(note_view)
        """The preview of the transcription in progress, if any."""

    def transcription_options(self, features: RequestedFeatures) -> TranscriptionOptions:
        """Capture the current language with the entry point's selected features."""
        return TranscriptionOptions(
            features.timestamps,
            features.diarization,
            language=self._preferences.current.language,
            context_bias=self._preferences.current.context_bias,
            realtime_delay_ms=self._preferences.current.realtime_delay_ms,
        )

    # Import

    def on_import_audio(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        run_task(self._pick_and_import(), self._window)

    def on_cancel_import(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        task = self._work.import_task
        if task is None or task.done() or not self._work.stage.can_cancel:
            return
        self._host.set_stage(Stage.CANCELLING)
        task.cancel()

    async def _pick_and_import(self) -> None:
        path = await pick_audio_file(self._window)
        choice = self._bottom_bar.choice
        if (
            path is None
            or choice is None
            or self._work.import_task is not None
            or self._work.recording_task is not None
        ):
            return
        note = self._host.note
        note_id = note.note_id if note else None
        options = self.transcription_options(self._bottom_bar.file_features)
        task = asyncio.ensure_future(self._import(path, note_id, choice, options))
        self._work.import_task = task
        self._host.set_stage(Stage.CONVERTING)
        self._host.show_content_pane()
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  # This coroutine itself was cancelled, not the import.
            self._host.toast(_("Import cancelled"))
        finally:
            self._work.import_task = None
            self._host.set_stage(Stage.IDLE)

    async def _import(
        self,
        path: Path,
        note_id: str | None,
        choice: ModelChoice,
        options: TranscriptionOptions,
    ) -> None:
        recording = Recording(
            "file-preview",
            datetime.now(UTC),
            timedelta(),
            RecordingSource.IMPORTED,
            choice.provider.name,
            choice.model.model_id,
        )
        self._begin(recording, choice, title=path.stem)
        try:
            await self._playback.finish()
            note = await self._workflow.import_file(
                path,
                note_id,
                choice,
                options,
                on_progress=self._on_progress,
                on_update=self.display.update,
            )
        except USER_FACING_ERRORS as error:
            log.warning("Import of %s failed: %s", path, error)
            show_error(self._window, str(error), _("Transcription Failed"))
            return
        finally:
            self._end()
        shown = self._host.note
        if shown is None or shown.note_id == note.note_id:
            self._host.select_note(note.note_id)
        else:
            self._host.toast(_("Transcription finished"))

    # Re-transcription

    def on_retranscribe_recording(
        self, _action: Gio.SimpleAction, param: GLib.Variant | None
    ) -> None:
        note = self._host.note
        if note is None or param is None:
            return
        recording = note.recording(param.get_string())
        if recording is None or recording.audio_path is None:
            self._host.toast(_("The original audio is not available."))
            return
        note_id = note.note_id

        def submit(choice: ModelChoice, features: RequestedFeatures) -> None:
            choices = available_models(self._providers.providers)
            refreshed = next((c for c in choices if c.key == choice.key), None)
            if refreshed is None or refreshed.model.capabilities.file is None:
                self._host.toast(_("Choose an available file transcription model."))
                return
            self._playback.stop()
            options = self.transcription_options(features)
            self._work.import_task = run_task(
                self._retranscribe(note_id, recording, refreshed, options), self._window
            )
            self._host.set_stage(Stage.TRANSCRIBING)

        dialog = RetranscribeDialog(
            self._providers,
            self._preferences,
            recording,
            note.recordings.index(recording) + 1,
            self._bottom_bar.choice,
            submit,
        )
        self._speakers.stop_playback_on_close(dialog)
        dialog.present(self._window)

    async def _retranscribe(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        self._begin(recording, choice, replacing=True)
        try:
            await self._playback.finish()
            note = await self._workflow.retranscribe(
                note_id,
                recording,
                choice,
                options,
                on_progress=self._on_progress,
                on_update=self.display.update,
            )
            self._end()
            shown = self._host.note
            if shown is not None and shown.note_id == note.note_id:
                self._host.show_note(note)
            else:
                self._host.toast(_("Transcription finished"))
        except asyncio.CancelledError:
            self._host.toast(_("Re-transcription cancelled"))
        except USER_FACING_ERRORS as error:
            log.warning("Re-transcription failed: %s", error)
            show_error(
                self._window,
                _("{error}\n\nYour text and audio have been kept.").format(error=error),
                _("Re-transcription Failed"),
            )
        finally:
            self._end()
            self._work.import_task = None
            self._host.set_stage(Stage.IDLE)

    # Retrying a recording that has no text yet

    def on_retry_recording(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        choice, note = self._bottom_bar.choice, self._host.note
        if (
            choice is None
            or note is None
            or param is None
            or self._work.import_task is not None
            or self._work.recording_task is not None
        ):
            return
        recording = note.recording(param.get_string())
        if recording is None or recording.transcription_state is TranscriptionState.READY:
            return
        options = self.transcription_options(self._bottom_bar.file_features)
        self._work.recording_task = run_task(
            self._retry(note.note_id, recording, choice, options), self._window
        )
        self._host.refresh_actions()

    async def _retry(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        try:
            await self.upload_recording(note_id, recording, choice, options)
        finally:
            self._work.recording_task = None
            self._host.set_stage(Stage.IDLE)

    async def upload_recording(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        """Transcribe a saved recording with a batch model, unless the window is closing."""
        if self._work.closing:
            return
        self._work.import_task = run_task(
            self._upload(note_id, recording, choice, options), self._window
        )
        self._host.set_stage(Stage.TRANSCRIBING)
        try:
            await self._work.import_task
        except asyncio.CancelledError:
            self._host.toast(_("Transcription cancelled"))
        finally:
            self._work.import_task = None

    async def _upload(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        self._begin(recording, choice, replacing=True)
        try:
            note = await self._workflow.transcribe_pending(
                note_id,
                recording,
                choice,
                options,
                on_progress=self._on_progress,
                on_update=self.display.update,
            )
        except USER_FACING_ERRORS as error:
            show_error(self._window, str(error), _("Transcription Failed"))
            return
        finally:
            self._end()
        shown = self._host.note
        if shown is not None and shown.note_id == note_id:
            self._host.show_note(note)

    # The preview on screen

    def _begin(
        self,
        recording: Recording,
        choice: ModelChoice,
        *,
        replacing: bool = False,
        title: str = "",
    ) -> None:
        self.display.begin(
            self._host.note,
            replace(recording, provider_name=choice.provider.name, model_id=choice.model.model_id),
            replacing=replacing,
        )
        if self._host.note is None:
            self._host.set_titles(title, title, _("Transcribing…"))
        self._host.refresh_content()
        self._host.show_content_pane()

    def _on_progress(self, stage: ImportStage, part: int, parts: int) -> None:
        if self._work.stage is Stage.CANCELLING:
            return
        step = _STAGES[stage]
        self._host.set_stage(step, stage_label(step, part, parts))

    def _end(self) -> None:
        if not self.display.active:
            return
        self.display.end()
        if self._host.note is None:
            self._host.set_titles(_("Kikoe"), "", "")
        self._host.refresh_content()
