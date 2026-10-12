"""Review local speaker labels without committing tentative choices."""

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.models import Note, Recording, SpeakerBinding
from kikoe.core.speakers import matching_candidates
from kikoe.i18n import _
from kikoe.storage.notes import NoteRepository
from kikoe.ui.speaker_row import SpeakerRow
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


class BindingItem(GObject.Object):
    """An immutable assignment for a model-bound matching row."""

    def __init__(self, binding: SpeakerBinding) -> None:
        super().__init__()
        self.binding = binding


@Gtk.Template(filename=ui_file("speaker_dialog"))
class SpeakerDialog(Adw.Dialog):
    """The HTML matching dialog with reversible many-to-one assignments."""

    __gtype_name__ = "KikoeSpeakerDialog"
    intro_label: Gtk.Label = Gtk.Template.Child()
    speaker_list: Gtk.ListBox = Gtk.Template.Child()
    apply_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self, notes: NoteRepository, note: Note, recording: Recording) -> None:
        super().__init__()
        self._notes, self._note, self._recording = notes, note, recording
        self._rows: list[SpeakerRow] = []
        number = note.recordings.index(recording) + 1
        self.intro_label.set_label(
            _(
                "Recording {number} has independently numbered speakers. "
                "Listen to the samples and choose who they are."
            ).format(number=number)
        )
        self._candidates = matching_candidates(note)
        self._items: Gio.ListStore[BindingItem] = Gio.ListStore(item_type=BindingItem)
        self._items.splice(0, 0, [BindingItem(b) for b in recording.speaker_bindings])
        self.speaker_list.bind_model(self._items, self._create_row)

    def _create_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, BindingItem)
        row = SpeakerRow(self._note, self._recording, item.binding, self._candidates)
        self._rows.append(row)
        return row

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    @Gtk.Template.Callback()
    def _on_apply_button_clicked(self, _button: Gtk.Button) -> None:
        self.apply_button.set_sensitive(False)
        run_task(self._apply(), self)

    async def _apply(self) -> None:
        try:
            await self._notes.match_speakers(
                self._note.note_id,
                self._recording.recording_id,
                {r.binding.source_id: r.selected for r in self._rows},
            )
            self.close()
        finally:
            self.apply_button.set_sensitive(True)
