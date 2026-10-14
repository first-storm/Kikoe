"""The notes list: search, row display, the row context menu and selection.

It owns no layout; the widgets come from the main window's template.
"""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from kikoe.core.models import NoteSummary
from kikoe.core.recording import CaptureState
from kikoe.i18n import _
from kikoe.storage.notes import NoteRepository
from kikoe.ui.guard import Guard
from kikoe.ui.items import NoteItem
from kikoe.ui.labels import capture_label, note_subtitle
from kikoe.ui.search import NoteSearch


class NoteSidebar:
    """Shows note summaries and tells the window which note the user chose."""

    def __init__(
        self,
        *,
        notes: NoteRepository,
        notes_list: Gtk.ListBox,
        placeholder: Adw.StatusPage,
        search_entry: Gtk.SearchEntry,
        search_bar: Gtk.SearchBar,
        split_view: Adw.NavigationSplitView,
        start_task: Callable[[Coroutine[None, None, None]], asyncio.Task[None]],
        current_note_id: Callable[[], str | None],
        capture_state: Callable[[str], CaptureState],
        locked: Callable[[], bool],
        open_note: Callable[[str], None],
    ) -> None:
        self._notes = notes
        self._list = notes_list
        self._placeholder = placeholder
        self._entry = search_entry
        self._bar = search_bar
        self._split_view = split_view
        self._current_note_id = current_note_id
        self._capture_state = capture_state
        self._locked = locked
        self._open_note = open_note
        self._search = NoteSearch(notes, start_task, search_entry.get_text, self.show)
        self._selecting = Guard()
        """Active while the selection is changed from code, not by the user."""
        self._items: Gio.ListStore[NoteItem] = Gio.ListStore(item_type=NoteItem)
        self._list.bind_model(self._items, self._create_row)
        self._bar.connect_entry(search_entry)
        self.allow_typing_to_search(True)

    def allow_typing_to_search(self, allowed: bool) -> None:
        """Typing in the list starts a search, which must not hide the Stop controls."""
        self._bar.set_key_capture_widget(self._list if allowed else None)

    def start_search(self) -> None:
        self._split_view.set_show_content(False)
        self._bar.set_search_mode(True)
        self._entry.grab_focus()

    def stop_search(self) -> None:
        self._bar.set_search_mode(False)

    def search_mode_changed(self) -> None:
        if not self._bar.get_search_mode():
            self._entry.set_text("")
            self.refresh()

    def refresh(self) -> None:
        """Run the current query again, or list every note when it is empty."""
        self._search.refresh()

    def close(self) -> None:
        """Abandon a query that is still running."""
        self._search.close()

    def show(self, notes: tuple[NoteSummary, ...]) -> None:
        with self._selecting:
            self._items.splice(0, self._items.get_n_items(), [NoteItem(s) for s in notes])
            searching = bool(self._entry.get_text().strip())
            self._placeholder.set_title(_("No Matching Notes") if searching else _("No Notes Yet"))
            self._placeholder.set_icon_name(
                "edit-find-symbolic" if searching else "document-edit-symbolic"
            )
        self.select(self._current_note_id())

    def show_all_and_select(self, note_id: str) -> bool:
        """Clear the search and select ``note_id``; False if it is not listed."""
        self._bar.set_search_mode(False)
        self._entry.set_text("")
        self.show(self._notes.notes)
        return self.select(note_id)

    def select(self, note_id: str | None) -> bool:
        """Select the row of ``note_id`` without reopening it; False if it is not listed."""
        index = next(
            (i for i, item in enumerate(self._items) if item.summary.note_id == note_id), None
        )
        row = self._list.get_row_at_index(index) if index is not None else None
        with self._selecting:
            if row is None:
                self._list.unselect_all()
            else:
                self._list.select_row(row)
        return row is not None

    def row_selected(self, row: Gtk.ListBoxRow | None) -> None:
        if self._selecting.active or row is None:
            return
        if self._locked():
            self.select(self._current_note_id())
            return
        item = self._items.get_item(row.get_index())
        if item is not None:
            self._open_note(item.summary.note_id)

    def _create_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, NoteItem)
        summary = item.summary
        title = Gtk.Label(label=summary.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        state = self._capture_state(summary.note_id)
        subtitle = Gtk.Label(
            label=note_subtitle(summary, datetime.now().astimezone())
            if state is CaptureState.STOPPED
            else capture_label(state),
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            css_classes=["caption", "dimmed"],
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6
        )
        box.append(title)
        if state is not CaptureState.STOPPED:
            subtitle.remove_css_class("dimmed")
            subtitle.add_css_class("recording-status")
            indicator = Gtk.Image(icon_name="media-record-symbolic", pixel_size=7)
            indicator.add_css_class("recording-indicator")
            status = Gtk.Box(spacing=6)
            status.append(indicator)
            status.append(subtitle)
            box.append(status)
        else:
            box.append(subtitle)

        # Secondary click and long press offer Rename and Delete without opening the note.
        def on_click(_gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
            self._popup_menu(box, summary.note_id, x, y)

        def on_long_press(_gesture: Gtk.GestureLongPress, x: float, y: float) -> None:
            self._popup_menu(box, summary.note_id, x, y)

        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", on_click)
        long_press = Gtk.GestureLongPress()
        long_press.connect("pressed", on_long_press)
        box.add_controller(secondary)
        box.add_controller(long_press)
        return box

    def _popup_menu(self, box: Gtk.Widget, note_id: str, x: float, y: float) -> None:
        row = box.get_ancestor(Gtk.ListBoxRow)
        assert isinstance(row, Gtk.ListBoxRow)
        target = GLib.Variant("s", note_id)
        menu = Gio.Menu()
        menu.append(_("_Rename…"), Gio.Action.print_detailed_name("win.rename-note-row", target))
        menu.append(
            _("_Delete Note"), Gio.Action.print_detailed_name("win.delete-note-row", target)
        )
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(box)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = round(x), round(y), 1, 1
        popover.set_pointing_to(rect)

        def release(_popover: Gtk.Popover) -> None:
            row.remove_css_class("has-open-popup")
            GLib.idle_add(popover.unparent)

        popover.connect("closed", release)
        # Keep the menu's target highlighted independently of pointer hover.
        row.add_css_class("has-open-popup")
        popover.popup()
