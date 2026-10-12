"""Discoverable keyboard shortcuts using the application's normal dialog styling."""

from gi.repository import Adw, Gtk

from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("shortcuts"))
class ShortcutsDialog(Adw.Dialog):
    """Keep shortcut labels accessible and responsive on narrow windows."""

    __gtype_name__ = "KikoeShortcutsDialog"
