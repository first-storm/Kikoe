"""The speech-to-text models of one engine, with related sizes folded into one row."""

from gi.repository import Adw, Gtk

from kikoe.core.local_models import (
    InstallStatus,
    LocalModelSpec,
    ModelFamily,
    ModelRole,
    group_by_family,
)
from kikoe.i18n import _, ngettext
from kikoe.services.local_models import LocalModelManager
from kikoe.ui.download_row import DownloadRow
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("local_model_list"))
class LocalModelList(Adw.PreferencesGroup):
    __gtype_name__ = "KikoeLocalModelList"

    def __init__(self, manager: LocalModelManager, engine_id: str) -> None:
        super().__init__()
        self._manager = manager
        self._families = group_by_family(manager.catalog.models_of(engine_id))
        self._expanders: dict[str, Adw.ExpanderRow] = {}
        self._rows: dict[str, DownloadRow] = {}
        for family in self._families:
            self._add_family(family)
        self.refresh()

    def _add_family(self, family: ModelFamily) -> None:
        rows = [create_model_row(self._manager, m) for m in family.models]
        self._rows.update((m.model_id, r) for m, r in zip(family.models, rows, strict=True))
        if len(rows) == 1:
            self.add(rows[0])
            return
        expander = Adw.ExpanderRow(title=family.name, use_markup=False)
        expander.set_expanded(any(self._is_present(m) for m in family.models))
        for row in rows:
            expander.add_row(row)
        self._expanders[family.name] = expander
        self.add(expander)

    def _is_present(self, model: LocalModelSpec) -> bool:
        return self._manager.model_state(model.model_id).status is not InstallStatus.MISSING

    def refresh(self) -> None:
        for family in self._families:
            for model in family.models:
                show_model(self._manager, self._rows[model.model_id], model, model_detail(model))
            expander = self._expanders.get(family.name)
            if expander is not None:
                expander.set_subtitle(self._family_summary(family))

    def _family_summary(self, family: ModelFamily) -> str:
        states = [self._manager.model_state(m.model_id).status for m in family.models]
        if InstallStatus.DOWNLOADING in states:
            return _("Downloading…")
        count = len(states)
        installed = states.count(InstallStatus.INSTALLED)
        if not installed:
            return ngettext("{count} model", "{count} models", count).format(count=count)
        return ngettext(
            "{installed} of {count} downloaded", "{installed} of {count} downloaded", count
        ).format(installed=installed, count=count)


def create_model_row(manager: LocalModelManager, model: LocalModelSpec) -> DownloadRow:
    """A row that downloads, cancels and removes ``model``; fill it with :func:`show_model`."""
    row = DownloadRow(model.display_name)

    def download() -> None:
        run_task(manager.install_model(model.model_id), row)

    def cancel() -> None:
        manager.cancel(model.model_id)

    def remove() -> None:
        confirm_model_removal(manager, model, row)

    row.on_download = download
    row.on_cancel = cancel
    row.on_remove = remove
    return row


def show_model(
    manager: LocalModelManager, row: DownloadRow, model: LocalModelSpec, detail: str
) -> None:
    """Show the model's download state, noting when its engine is missing."""
    state = manager.model_state(model.model_id)
    note = ""
    engine = manager.catalog.engine(model.engine_id)
    engine_ready = manager.engine_state(model.engine_id).status in (
        InstallStatus.INSTALLED,
        InstallStatus.BUNDLED,
    )
    if state.status is InstallStatus.INSTALLED and engine and not engine_ready:
        note = _("Needs {engine}").format(engine=engine.display_name)
    row.show_state(state, model.size, detail, note)


def confirm_model_removal(
    manager: LocalModelManager, model: LocalModelSpec, parent: Gtk.Widget
) -> None:
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
            run_task(manager.remove_model(model.model_id), parent)

    dialog.connect("response", respond)
    dialog.present(parent)


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
