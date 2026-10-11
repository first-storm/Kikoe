"""The main window: notes sidebar on the left, the selected note on the right."""

from collections.abc import Callable
from datetime import datetime

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from kikoe.core.models import DisplayOptions, Note
from kikoe.i18n import _
from kikoe.storage.notes import NoteRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.items import NoteItem
from kikoe.ui.labels import note_subtitle, note_title_subtitle
from kikoe.ui.note_view import NoteView
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file

_NOTE_ACTIONS = ("rename-note", "delete-note")
"""Window actions that act on the shown note and are disabled without one."""


@Gtk.Template(filename=ui_file("window"))
class Window(Adw.ApplicationWindow):
    __gtype_name__ = "KikoeWindow"

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    split_view: Adw.NavigationSplitView = Gtk.Template.Child()
    notes_list: Gtk.ListBox = Gtk.Template.Child()
    content_page: Adw.NavigationPage = Gtk.Template.Child()
    window_title: Adw.WindowTitle = Gtk.Template.Child()
    note_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    display_options_button: Gtk.MenuButton = Gtk.Template.Child()
    timestamps_row: Adw.SwitchRow = Gtk.Template.Child()
    speakers_row: Adw.SwitchRow = Gtk.Template.Child()
    content_stack: Gtk.Stack = Gtk.Template.Child()
    note_view_slot: Adw.Bin = Gtk.Template.Child()

    def __init__(
        self, application: Gtk.Application, providers: ProviderRepository, notes: NoteRepository
    ) -> None:
        super().__init__(application=application)
        # Added from code: Blueprint cannot tell the custom widget scrolls by itself.
        self.note_view = NoteView()
        self.note_view_slot.set_child(self.note_view)
        self._providers = providers
        self._notes = notes
        self._note: Note | None = None
        self._providers_loaded = False
        self._selecting = False
        """True while the sidebar selection is changed from code, not by the user."""
        self._showing = False
        """True while widgets are filled from a note, so their signals are not user input."""
        self._note_items: Gio.ListStore[NoteItem] = Gio.ListStore(item_type=NoteItem)
        self.notes_list.bind_model(self._note_items, self._create_note_row)
        self._add_action("new-note", self._on_new_note)
        self._add_action("rename-note", self._on_rename_note)
        self._add_action("delete-note", self._on_delete_note)
        self._show_note(None)
        providers.connect_changed(self._on_providers_changed)
        notes.connect_changed(self._on_notes_changed)

    @property
    def note(self) -> Note | None:
        """The note shown in the content pane."""
        return self._note

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_overlay.add_toast(toast)

    def select_note(self, note_id: str) -> None:
        """Select ``note_id`` in the sidebar and show it."""
        if self._select_row(note_id):
            run_task(self._open_note(note_id), self)
            self.split_view.set_show_content(True)

    def _add_action(
        self, name: str, callback: Callable[[Gio.SimpleAction, GLib.Variant | None], None]
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    # Sidebar

    def _create_note_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, NoteItem)
        summary = item.summary
        title = Gtk.Label(label=summary.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        subtitle = Gtk.Label(
            label=note_subtitle(summary, datetime.now().astimezone()),
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            css_classes=["caption", "dim-label"],
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6
        )
        box.append(title)
        box.append(subtitle)
        return box

    def _select_row(self, note_id: str | None) -> bool:
        """Select the row of ``note_id`` without reopening it; False if it is not listed."""
        index = next(
            (i for i, item in enumerate(self._note_items) if item.summary.note_id == note_id),
            None,
        )
        row = self.notes_list.get_row_at_index(index) if index is not None else None
        self._selecting = True
        try:
            if row is None:
                self.notes_list.unselect_all()
            else:
                self.notes_list.select_row(row)
        finally:
            self._selecting = False
        return row is not None

    @Gtk.Template.Callback()
    def _on_notes_list_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._selecting or row is None:
            return
        item = self._note_items.get_item(row.get_index())
        if item is not None:
            run_task(self._open_note(item.summary.note_id), self)
            self.split_view.set_show_content(True)

    def _on_notes_changed(self, note_id: str | None) -> None:
        self._note_items.splice(
            0, self._note_items.get_n_items(), [NoteItem(s) for s in self._notes.notes]
        )
        current = self._note.note_id if self._note else None
        if current is None:
            self._update_content()
            return
        if not self._select_row(current):  # The shown note was deleted.
            self._show_note(None)
            self.split_view.set_show_content(False)
        elif note_id == current:
            run_task(self._open_note(current), self)

    def _on_providers_changed(self) -> None:
        self._providers_loaded = True
        self._update_content()

    # Content

    async def _open_note(self, note_id: str) -> None:
        self._show_note(await self._notes.get_note(note_id))

    def _show_note(self, note: Note | None) -> None:
        self._note = note
        self.note_menu_button.set_visible(note is not None)
        self.display_options_button.set_visible(note is not None)
        for name in _NOTE_ACTIONS:
            action = self.lookup_action(name)
            if isinstance(action, Gio.SimpleAction):
                action.set_enabled(note is not None)
        if note is None:
            self.content_page.set_title(_("Kikoe"))
            self.window_title.set_title(_("Kikoe"))
            self.window_title.set_subtitle("")
        else:
            self.content_page.set_title(note.title)
            self.window_title.set_title(note.title)
            self.window_title.set_subtitle(note_title_subtitle(note))
            self._showing = True
            try:
                self.timestamps_row.set_active(note.display.show_timestamps)
                self.speakers_row.set_active(note.display.show_speakers)
            finally:
                self._showing = False
            self.note_view.show_note(note)
        self._update_content()

    @Gtk.Template.Callback()
    def _on_display_row_notify_active(
        self, _row: Adw.SwitchRow, _pspec: GObject.ParamSpec | None
    ) -> None:
        if self._showing or self._note is None:
            return
        options = DisplayOptions(self.timestamps_row.get_active(), self.speakers_row.get_active())
        if options != self._note.display:
            run_task(self._notes.set_display_options(self._note.note_id, options), self)

    def _update_content(self) -> None:
        if self._note is not None:
            page = "note"
        elif not self._providers_loaded:
            page = "loading"
        elif not self._providers.providers:
            page = "welcome"
        else:
            page = "empty"
        self.content_stack.set_visible_child_name(page)

    # Note actions

    def _on_new_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        run_task(self._create_note(), self)

    async def _create_note(self) -> None:
        note = await self._notes.create_note(_("New Note"))
        self.select_note(note.note_id)

    def _on_rename_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        note = self._note
        if note is None:
            return
        entry = Gtk.Entry(text=note.title, activates_default=True)
        dialog = Adw.AlertDialog(heading=_("Rename Note"), extra_child=entry)
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("rename", _("_Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def update_enabled(_entry: Gtk.Entry) -> None:
            dialog.set_response_enabled("rename", bool(entry.get_text().strip()))

        def respond(_dialog: Adw.AlertDialog, response: str) -> None:
            title = entry.get_text().strip()
            if response == "rename" and title and title != note.title:
                run_task(self._notes.rename_note(note.note_id, title), self)

        entry.connect("changed", update_enabled)
        dialog.connect("response", respond)
        dialog.present(self)
        entry.grab_focus()

    def _on_delete_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        note = self._note
        if note is None:
            return
        dialog = Adw.AlertDialog(
            heading=_("Delete “{title}”?").format(title=note.title),
            body=_("Its transcript and original audio will be deleted. This cannot be undone."),
        )
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("delete", _("_Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "delete":
                run_task(self._notes.delete_note(note.note_id), self)

        dialog.connect("response", respond)
        dialog.present(self)
