"""One local engine in Preferences: its setup, its models and its removal."""

from gi.repository import Adw, GLib, Gtk

from kikoe.core.local_models import EngineSpec, InstallStatus, LocalModelSpec, ModelRole
from kikoe.i18n import _, ngettext
from kikoe.services.local_models import LocalModelManager
from kikoe.ui.download_row import DownloadRow
from kikoe.ui.local_engine_group import LocalEngineGroup
from kikoe.ui.local_model_list import LocalModelList, create_model_row, show_model
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("local_engine_page"))
class LocalEnginePage(Adw.NavigationPage):
    __gtype_name__ = "KikoeLocalEnginePage"

    page: Adw.PreferencesPage = Gtk.Template.Child()
    speakers_group: Adw.PreferencesGroup = Gtk.Template.Child()
    remove_group: Adw.PreferencesGroup = Gtk.Template.Child()

    def __init__(self, manager: LocalModelManager, engine: EngineSpec) -> None:
        super().__init__(title=engine.display_name)
        self._manager = manager
        self._engine = engine
        self._engine_group = LocalEngineGroup(manager, engine)
        self._models = LocalModelList(manager, engine.engine_id)
        # Before the Blueprint's groups, which close the page.
        self.page.insert(self._engine_group, 0)
        self.page.insert(self._models, 1)
        self._speakers: list[tuple[LocalModelSpec, DownloadRow]] = []
        for model in manager.catalog.models_of(engine.engine_id):
            if model.role is ModelRole.DIARIZE:
                row = create_model_row(manager, model)
                self._speakers.append((model, row))
                self.speakers_group.add(row)
        self.speakers_group.set_visible(bool(self._speakers))
        self._disconnect = manager.connect_changed(self._refresh)
        self._refresh()

    def _refresh(self) -> None:
        self._engine_group.refresh()
        self._models.refresh()
        for model, row in self._speakers:
            show_model(self._manager, row, model, "")
        status = self._manager.engine_state(self._engine.engine_id).status
        self.remove_group.set_visible(status is InstallStatus.INSTALLED)

    @Gtk.Template.Callback()
    def _on_page_hidden(self, _page: Adw.NavigationPage) -> None:
        self._disconnect()

    @Gtk.Template.Callback()
    def _on_remove_row_activated(self, _row: Adw.ButtonRow) -> None:
        engine = self._engine
        models = self._manager.installed_models(engine.engine_id)
        dialog = Adw.AlertDialog(
            heading=_("Remove {engine}?").format(engine=engine.display_name),
            body=_("Local models cannot be used until the engine is downloaded again."),
        )
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("engine", _("_Remove Engine"))
        dialog.set_response_appearance("engine", Adw.ResponseAppearance.DESTRUCTIVE)
        if models:
            size = GLib.format_size(sum(m.size for m in models))
            label = ngettext(
                "Remove Engine and {count} _Model ({size})",
                "Remove Engine and {count} _Models ({size})",
                len(models),
            ).format(count=len(models), size=size)
            dialog.add_response("all", label)
            dialog.set_response_appearance("all", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_body(_("Kept models work again once the engine is downloaded again."))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_response)
        dialog.present(self)

    def _on_remove_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response in ("engine", "all"):
            removal = self._manager.remove_engine(
                self._engine.engine_id, with_models=response == "all"
            )
            run_task(removal, self)
