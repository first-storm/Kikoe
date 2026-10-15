"""Preferences page for engines and models that run on this device."""

from gi.repository import Adw, Gtk

from kikoe.services.local_models import LocalModelManager
from kikoe.ui.local_engine_group import LocalEngineGroup
from kikoe.ui.local_model_list import LocalModelList
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("local_models_page"))
class LocalModelsPage(Adw.PreferencesPage):
    __gtype_name__ = "KikoeLocalModelsPage"

    unsupported_group: Adw.PreferencesGroup = Gtk.Template.Child()

    def __init__(self, manager: LocalModelManager) -> None:
        super().__init__()
        self._manager = manager
        self._engines = [LocalEngineGroup(manager, engine) for engine in manager.engines]
        for group in self._engines:
            self.add(group)
        self._models = LocalModelList(manager)
        if manager.models:
            self.add(self._models)
        self.unsupported_group.set_visible(not self._engines)
        self._disconnect = manager.connect_changed(self._refresh)

    def _refresh(self) -> None:
        for group in self._engines:
            group.refresh()
        self._models.refresh()

    def close(self) -> None:
        """Stop following the manager once the dialog is gone."""
        self._disconnect()
