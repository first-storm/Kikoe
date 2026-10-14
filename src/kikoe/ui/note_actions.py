"""Create, rename and delete notes and recordings from the main window's actions."""

from gi.repository import Adw, Gio, GLib, Gtk

from kikoe.i18n import _
from kikoe.services.playback import Playback
from kikoe.storage.notes import NoteRepository
from kikoe.ui.dialogs import alert_dialog, confirm_destructive
from kikoe.ui.tasks import run_task
from kikoe.ui.window_host import WindowHost


class NoteActions:
    """Asks for confirmation where the change cannot be undone, then applies it."""

    def __init__(
        self,
        window: Adw.ApplicationWindow,
        host: WindowHost,
        notes: NoteRepository,
        playback: Playback,
    ) -> None:
        self._window = window
        self._host = host
        self._notes = notes
        self._playback = playback

    def on_new_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        run_task(self._create_note(), self._window)

    async def _create_note(self) -> None:
        note = await self._notes.create_note(_("New Note"))
        self._host.select_note(note.note_id)

    def on_rename_note(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if target := self._target(param):
            self._rename(*target)

    def _target(self, param: GLib.Variant | None) -> tuple[str, str] | None:
        """ID and title of the sidebar row's note (``*-row`` actions) or else the shown note."""
        if param is None:
            note = self._host.note
        else:
            note = next((s for s in self._notes.notes if s.note_id == param.get_string()), None)
        return (note.note_id, note.title) if note is not None else None

    def _rename(self, note_id: str, current_title: str) -> None:
        entry = Gtk.Entry(text=current_title, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Note Title")])

        def rename() -> None:
            title = entry.get_text().strip()
            if title and title != current_title:
                run_task(self._notes.rename_note(note_id, title), self._window)

        dialog = alert_dialog(_("Rename Note"), "", _("_Cancel"), _("_Rename"), rename)
        dialog.set_extra_child(entry)

        def update_enabled(_entry: Gtk.Entry) -> None:
            dialog.set_response_enabled("confirm", bool(entry.get_text().strip()))

        entry.connect("changed", update_enabled)
        dialog.present(self._window)
        entry.grab_focus()

    def on_delete_note(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if target := self._target(param):
            self._delete(*target)

    def _delete(self, note_id: str, title: str) -> None:
        confirm_destructive(
            self._window,
            _("Delete “{title}”?").format(title=title),
            _("Its transcript and original audio will be deleted. This cannot be undone."),
            _("_Delete"),
            lambda: run_task(self._notes.delete_note(note_id), self._window),
        )

    def on_delete_recording(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        note = self._host.note
        if note is None or param is None:
            return
        recording_id = param.get_string()
        recording = note.recording(recording_id)
        if recording is None:
            self._host.toast(_("This recording is no longer available."))
            return
        number = note.recordings.index(recording) + 1
        self._playback.stop()
        confirm_destructive(
            self._window,
            _("Delete Recording {number}?").format(number=number),
            _("Its transcript and original audio will be deleted. This cannot be undone."),
            _("_Delete"),
            lambda: run_task(self._delete_recording(note.note_id, recording_id), self._window),
        )

    async def _delete_recording(self, note_id: str, recording_id: str) -> None:
        self._host.show_note(await self._notes.delete_recording(note_id, recording_id))
