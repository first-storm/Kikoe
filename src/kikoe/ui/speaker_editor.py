"""Edit a shared person name or review this recording's identity assignments."""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from kikoe.core.models import Note
from kikoe.storage.notes import NoteRepository
from kikoe.ui.speaker_row import speaker_label
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("speaker_editor"))
class SpeakerEditor(Adw.Dialog):
    """Renaming changes the person, while matching changes recognition labels."""

    __gtype_name__ = "KikoeSpeakerEditor"
    name_row: Adw.EntryRow = Gtk.Template.Child()
    save_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self, notes: NoteRepository, note: Note, person: str, match: Callable[[], None]
    ) -> None:
        super().__init__()
        self._notes, self._note, self._person, self._match = notes, note, person, match
        self.name_row.set_text(speaker_label(note, person))

    @Gtk.Template.Callback()
    def _on_name_row_changed(self, _row: Adw.EntryRow) -> None:
        self.save_button.set_sensitive(bool(self.name_row.get_text().strip()))

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    @Gtk.Template.Callback()
    def _on_save_button_clicked(self, _button: Gtk.Button) -> None:
        self.save_button.set_sensitive(False)
        run_task(self._save(), self)

    async def _save(self) -> None:
        try:
            await self._notes.rename_speaker(
                self._note.note_id, self._person, self.name_row.get_text()
            )
            self.close()
        finally:
            self.save_button.set_sensitive(bool(self.name_row.get_text().strip()))

    @Gtk.Template.Callback()
    def _on_match_row_activated(self, _row: Adw.ButtonRow) -> None:
        self.close()
        self._match()
