"""The models of every available engine, each with its own download row."""

from gi.repository import Adw, Gtk

from kikoe.core.local_models import LocalModelSpec
from kikoe.i18n import _, ngettext
from kikoe.services.local_models import LocalModelManager
from kikoe.ui.download_row import DownloadRow
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
            self._rows[model.model_id] = row
            self.models_list.append(row)
        self.refresh()

    def refresh(self) -> None:
        for model in self._manager.models:
            row = self._rows[model.model_id]
            row.show_state(
                self._manager.model_state(model.model_id), model.size, model_detail(model)
            )


def model_detail(model: LocalModelSpec) -> str:
    """E.g. “Multilingual · Timestamps · Live”."""
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
