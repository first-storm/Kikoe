"""Ask for a title before saving a new note."""

from collections.abc import Awaitable, Callable

from gi.repository import Adw, Gio, GLib, Gtk

from kikoe.i18n import _
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("new_note_dialog"))
class NewNoteDialog(Adw.Dialog):
    """Keep the title available for retry if saving fails."""

    __gtype_name__ = "KikoeNewNoteDialog"

    title_row: Adw.EntryRow = Gtk.Template.Child()
    create_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self, create: Callable[[str], Awaitable[None]]) -> None:
        super().__init__()
        self._create = create
        self._saving = False
        self._create_action = Gio.SimpleAction.new("create", None)
        self._create_action.connect("activate", self._on_create_action_activate)
        actions = Gio.SimpleActionGroup()
        actions.add_action(self._create_action)
        self.insert_action_group("note", actions)
        self.title_row.set_text(_("New Note"))
        self.title_row.grab_focus()
        self.title_row.select_region(0, -1)

    @Gtk.Template.Callback()
    def _on_title_row_changed(self, _row: Adw.EntryRow) -> None:
        self._create_action.set_enabled(
            not self._saving and bool(self.title_row.get_text().strip())
        )

    def _on_create_action_activate(
        self, _action: Gio.SimpleAction, _parameter: GLib.Variant | None
    ) -> None:
        title = self.title_row.get_text().strip()
        if self._saving or not title:
            return
        self._saving = True
        self._create_action.set_enabled(False)
        self.title_row.set_sensitive(False)
        self.set_can_close(False)
        run_task(self._save(title), self)

    async def _save(self, title: str) -> None:
        try:
            await self._create(title)
            self.force_close()
        finally:
            self._saving = False
            self.title_row.set_sensitive(True)
            self.set_can_close(True)
            self._on_title_row_changed(self.title_row)
