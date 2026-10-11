"""The main window: notes sidebar on the left, the selected note on the right."""

from gi.repository import Adw, Gtk

from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("window"))
class Window(Adw.ApplicationWindow):
    __gtype_name__ = "KikoeWindow"

    split_view: Adw.NavigationSplitView = Gtk.Template.Child()
    notes_list: Gtk.ListBox = Gtk.Template.Child()
    content_stack: Gtk.Stack = Gtk.Template.Child()
