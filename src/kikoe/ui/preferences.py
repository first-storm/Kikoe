"""Preferences: general settings and transcription providers."""

from gi.repository import Adw, Gtk

from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("preferences"))
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "KikoePreferencesDialog"

    providers_group: Adw.PreferencesGroup = Gtk.Template.Child()
