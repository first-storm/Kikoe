"""The Find and Replace bar above the open note's transcript.

It only holds what to find and what to put instead; `ui.find_controller` searches the
note, and the bar's buttons trigger the window actions it registers.
"""

from gi.repository import Adw, Gdk, GObject, Gtk

from kikoe.i18n import _
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("find_bar"))
class FindBar(Adw.Bin):
    __gtype_name__ = "KikoeFindBar"

    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    find_entry: Gtk.SearchEntry = Gtk.Template.Child()
    match_label: Gtk.Label = Gtk.Template.Child()
    replace_entry: Gtk.Entry = Gtk.Template.Child()

    __gsignals__ = {  # noqa: RUF012  # PyGObject reads this class attribute once
        # The text to find changed, after the entry's typing delay.
        "query-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        # The bar was closed, with Escape or its close button.
        "closed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self) -> None:
        super().__init__()
        self.search_bar.connect_entry(self.find_entry)

    @property
    def is_open(self) -> bool:
        return self.search_bar.get_search_mode()

    @property
    def query(self) -> str:
        return self.find_entry.get_text()

    @property
    def replacement(self) -> str:
        return self.replace_entry.get_text()

    def open(self, query: str | None = None) -> None:
        """Show the bar and focus the find entry, its text selected to type over."""
        if query is not None:
            self.find_entry.set_text(query)
        self.search_bar.set_search_mode(True)
        self.find_entry.grab_focus()
        self.find_entry.select_region(0, -1)

    def close(self) -> None:
        self.search_bar.set_search_mode(False)

    def show_count(self, text: str, *, invalid: bool = False) -> None:
        """Show how many matches there are, or that the pattern cannot be used."""
        self.match_label.set_label(text)
        if invalid:
            self.find_entry.add_css_class("error")
        else:
            self.find_entry.remove_css_class("error")
        self.find_entry.update_property([Gtk.AccessibleProperty.DESCRIPTION], [text])

    def mark_replacement_invalid(self, invalid: bool) -> None:
        if invalid:
            self.replace_entry.add_css_class("error")
        else:
            self.replace_entry.remove_css_class("error")

    def set_regex(self, regex: bool) -> None:
        """Explain group references while the query is a regular expression."""
        self.replace_entry.set_tooltip_text(
            _("Use \\1 or \\g<name> to insert a group of the match") if regex else None
        )
        self.mark_replacement_invalid(False)

    @Gtk.Template.Callback()
    def _on_search_bar_notify_search_mode_enabled(
        self, _bar: Gtk.SearchBar, _spec: GObject.ParamSpec
    ) -> None:
        if not self.search_bar.get_search_mode():
            self.emit("closed")

    @Gtk.Template.Callback()
    def _on_find_entry_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self.emit("query-changed")

    @Gtk.Template.Callback()
    def _on_find_entry_activate(self, _entry: Gtk.SearchEntry) -> None:
        self.activate_action("win.find-next", None)

    @Gtk.Template.Callback()
    def _on_find_entry_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if (
            keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter)
            and modifiers == Gdk.ModifierType.SHIFT_MASK
        ):
            self.activate_action("win.find-previous", None)
            return True
        return False

    @Gtk.Template.Callback()
    def _on_replace_entry_changed(self, _entry: Gtk.Entry) -> None:
        self.mark_replacement_invalid(False)

    @Gtk.Template.Callback()
    def _on_replace_entry_activate(self, _entry: Gtk.Entry) -> None:
        self.activate_action("win.replace", None)

    @Gtk.Template.Callback()
    def _on_bar_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        _state: Gdk.ModifierType,
    ) -> bool:
        # The find entry closes the bar itself; this covers the replace entry and buttons.
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
