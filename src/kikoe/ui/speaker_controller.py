"""Playback and speaker-identity actions for the note shown in the main window."""

from gi.repository import Adw, Gio, GLib

from kikoe.core.models import Recording
from kikoe.core.speakers import identity_for
from kikoe.i18n import _
from kikoe.services.playback import Playback
from kikoe.storage.notes import NoteRepository
from kikoe.storage.preferences import PreferencesRepository
from kikoe.ui.speaker_dialog import SpeakerDialog
from kikoe.ui.speaker_editor import SpeakerEditor
from kikoe.ui.window_host import WindowHost


class SpeakerController:
    """Plays recordings and opens the dialogs that name and match their speakers."""

    def __init__(
        self,
        window: Adw.ApplicationWindow,
        host: WindowHost,
        notes: NoteRepository,
        preferences: PreferencesRepository,
        playback: Playback,
    ) -> None:
        self._window = window
        self._host = host
        self._notes = notes
        self._preferences = preferences
        self._playback = playback

    def stop_playback_on_close(self, dialog: Adw.Dialog) -> None:
        """Samples played from a dialog must not outlive it."""
        dialog.connect("closed", self._on_dialog_closed)

    def _on_dialog_closed(self, _dialog: Adw.Dialog) -> None:
        self._playback.stop()

    def _recording(self, recording_id: str) -> Recording | None:
        note = self._host.note
        return note.recording(recording_id) if note else None

    def on_play_recording(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is not None:
            self._request_playback(param.get_string(), 0, None)

    def on_play_sample(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is not None:
            recording_id, start, end = param.unpack()
            self._request_playback(str(recording_id), float(start), float(end))

    def _request_playback(self, recording_id: str, start: float, end: float | None) -> None:
        recording = self._recording(recording_id)
        if recording is None or recording.audio_path is None:
            self._host.toast(_("The original audio is not available."))
            return
        self._playback.play(
            recording.audio_path,
            recording_id,
            start,
            end,
            self._preferences.current.output_device,
        )

    def on_edit_speaker(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        note = self._host.note
        if note is None or param is None:
            return
        recording_id, source_id = param.unpack()
        recording = self._recording(str(recording_id))
        if recording is None:
            return
        person = identity_for(recording, str(source_id))
        if not any(s.speaker_id == person for s in note.speakers):
            return
        dialog = SpeakerEditor(
            self._notes, note, person, lambda: self._present_matching(str(recording_id))
        )
        self.stop_playback_on_close(dialog)
        dialog.present(self._window)

    def on_match_speakers(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is not None:
            self._present_matching(param.get_string())

    def _present_matching(self, recording_id: str) -> None:
        note = self._host.note
        recording = self._recording(recording_id)
        if note is None or recording is None or not recording.speaker_bindings:
            self._host.toast(_("This recording has no detected speakers."))
            return
        dialog = SpeakerDialog(self._notes, note, recording)
        self.stop_playback_on_close(dialog)
        dialog.present(self._window)
