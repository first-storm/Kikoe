"""Keyboard shortcuts dialog, using the stock libadwaita shortcuts window."""

from gi.repository import Adw, Gtk

from kikoe.ui.templates import ui_file


def create_shortcuts_dialog() -> Adw.ShortcutsDialog:
    """``Adw.ShortcutsDialog`` is final, so it is loaded from its Blueprint, not subclassed."""
    builder = Gtk.Builder.new_from_file(ui_file("shortcuts"))
    dialog = builder.get_object("shortcuts_dialog")
    assert isinstance(dialog, Adw.ShortcutsDialog)
    return dialog
