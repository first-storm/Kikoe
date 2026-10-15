"""The models of every available engine, each with its own download row."""

from gi.repository import Adw, Gtk

from kikoe.core.local_models import InstallStatus, LocalModelSpec, ModelRole
from kikoe.i18n import _, ngettext
from kikoe.services.local_models import LocalModelManager
from kikoe.ui.download_row import DownloadRow
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("local_model_list"))
class LocalModelList(Adw.PreferencesGroup):
    __gtype_name__ = "KikoeLocalModelList"

    models_list: Gtk.ListBox = Gtk.Template.Child()

    def __init__(self, manager: LocalModelManager) -> None:
        super().__init__()
        self._manager = manager
        self._rows: dict[str, DownloadRow] = {}
        for model in manager.models:
            row = DownloadRow(model.display_name)
            row.on_download = lambda m=model: self._download(m)
            row.on_cancel = lambda m=model: manager.cancel(m.model_id)
            row.on_remove = lambda m=model: self._confirm_removal(m)
            self._rows[model.model_id] = row
            self.models_list.append(row)
        self.refresh()

    def refresh(self) -> None:
        for model in self._manager.models:
            state = self._manager.model_state(model.model_id)
            note = ""
            engine = self._manager.catalog.engine(model.engine_id)
            engine_ready = self._manager.engine_state(model.engine_id).status in (
                InstallStatus.INSTALLED,
                InstallStatus.BUNDLED,
            )
            if state.status is InstallStatus.INSTALLED and engine and not engine_ready:
                note = _("Needs {engine}").format(engine=engine.display_name)
            self._rows[model.model_id].show_state(state, model.size, model_detail(model), note)

    def _download(self, model: LocalModelSpec) -> None:
        run_task(self._manager.install_model(model.model_id), self)

    def _confirm_removal(self, model: LocalModelSpec) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove {model}?").format(model=model.display_name),
            body=_("You can download it again later."),
        )
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("remove", _("_Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "remove":
                run_task(self._manager.remove_model(model.model_id), self)

        dialog.connect("response", respond)
        dialog.present(self)


def model_detail(model: LocalModelSpec) -> str:
    """E.g. “Multilingual · Timestamps · Realtime”."""
    if model.role is ModelRole.DIARIZE:
        return _("Adds speakers to models with timestamps")
    if model.languages:
        count = len(model.languages)
        languages = (
            ", ".join(model.languages)
            if count <= 3
            else ngettext("{count} language", "{count} languages", count).format(count=count)
        )
    else:
        languages = _("Multilingual")
    parts = [languages]
    if model.timestamps:
        parts.append(_("Timestamps"))
    if model.streaming:
        parts.append(_("Realtime"))
    return " · ".join(parts)
