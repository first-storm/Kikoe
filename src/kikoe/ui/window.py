"""The main window: notes sidebar on the left, the selected note on the right."""

from gi.repository import Adw, Gtk

from kikoe.storage.providers import ProviderRepository
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("window"))
class Window(Adw.ApplicationWindow):
    __gtype_name__ = "KikoeWindow"

    split_view: Adw.NavigationSplitView = Gtk.Template.Child()
    notes_list: Gtk.ListBox = Gtk.Template.Child()
    content_stack: Gtk.Stack = Gtk.Template.Child()

    def __init__(self, application: Gtk.Application, repository: ProviderRepository) -> None:
        super().__init__(application=application)
        self._repository = repository
        self.content_stack.set_visible_child_name("loading")
        repository.connect_changed(self._on_providers_changed)

    def _on_providers_changed(self) -> None:
        """Show the welcome page until there is at least one provider to transcribe with."""
        page = "empty" if self._repository.providers else "welcome"
        self.content_stack.set_visible_child_name(page)
