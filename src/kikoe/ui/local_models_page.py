"""Preferences page for engines and models that run on this device.

The page lists the engines, each opening its own page of settings and models, and
every model on this device, so that space can be freed without visiting each engine.
"""

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from kikoe.core.local_models import EngineSpec, InstallStatus, LocalModelSpec
from kikoe.i18n import ngettext
from kikoe.services.local_models import LocalModelManager
from kikoe.ui.download_row import DownloadRow
from kikoe.ui.items import LocalModelItem
from kikoe.ui.local_engine_group import engine_summary
from kikoe.ui.local_engine_page import LocalEnginePage
from kikoe.ui.local_model_list import create_model_row, show_model
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("local_models_page"))
class LocalModelsPage(Adw.PreferencesPage):
    __gtype_name__ = "KikoeLocalModelsPage"

    engines_group: Adw.PreferencesGroup = Gtk.Template.Child()
    downloaded_group: Adw.PreferencesGroup = Gtk.Template.Child()
    downloaded_list: Gtk.ListBox = Gtk.Template.Child()
    unsupported_group: Adw.PreferencesGroup = Gtk.Template.Child()

    def __init__(self, manager: LocalModelManager) -> None:
        super().__init__()
        self._manager = manager
        self._engine_rows: list[tuple[EngineSpec, Adw.ActionRow]] = []
        for engine in manager.engines:
            row = self._create_engine_row(engine)
            self._engine_rows.append((engine, row))
            self.engines_group.add(row)
        supported = bool(self._engine_rows)
        self.engines_group.set_visible(supported)
        self.downloaded_group.set_visible(supported)
        self.unsupported_group.set_visible(not supported)
        self._downloaded: Gio.ListStore[LocalModelItem] = Gio.ListStore(item_type=LocalModelItem)
        self._model_rows: dict[str, DownloadRow] = {}
        """Kept across refreshes, so progress updates do not rebuild rows or move focus."""
        self.downloaded_list.bind_model(self._downloaded, self._create_model_row)
        self._disconnect = manager.connect_changed(self._refresh)
        self._refresh()

    def _create_engine_row(self, engine: EngineSpec) -> Adw.ActionRow:
        row = Adw.ActionRow(title=engine.display_name, use_markup=False, activatable=True)
        row.set_subtitle_lines(2)
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))

        def open_engine(_row: Adw.ActionRow) -> None:
            dialog = self.get_ancestor(Adw.PreferencesDialog)
            if isinstance(dialog, Adw.PreferencesDialog):
                dialog.push_subpage(LocalEnginePage(self._manager, engine))

        row.connect("activated", open_engine)
        return row

    def _create_model_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, LocalModelItem)
        model = item.model
        row = self._model_rows.get(model.model_id)
        if row is None:
            row = create_model_row(self._manager, model)
            self._model_rows[model.model_id] = row
        return row

    def _refresh(self) -> None:
        manager = self._manager
        for engine, row in self._engine_rows:
            row.set_subtitle(engine_summary(manager, engine))
            installed = manager.engine_state(engine.engine_id).status in (
                InstallStatus.INSTALLED,
                InstallStatus.BUNDLED,
            )
            if installed and manager.probe_result(engine.engine_id) is None:
                run_task(manager.probe(engine.engine_id), self)
        present = [
            m
            for m in manager.models
            if manager.model_state(m.model_id).status is not InstallStatus.MISSING
        ]
        self._show_models(present)

    def _show_models(self, models: list[LocalModelSpec]) -> None:
        shown = [self._downloaded.get_item(i) for i in range(self._downloaded.get_n_items())]
        if [i.model.model_id for i in shown if i] != [m.model_id for m in models]:
            items = [LocalModelItem(m) for m in models]
            self._downloaded.splice(0, self._downloaded.get_n_items(), items)
        for model in models:
            engine = self._manager.catalog.engine(model.engine_id)
            row = self._model_rows.get(model.model_id)
            if row is not None:
                show_model(self._manager, row, model, engine.display_name if engine else "")
        installed = self._manager.installed_models()
        size = sum(m.size for m in installed)
        self.downloaded_group.set_description(
            ngettext("{count} model · {size}", "{count} models · {size}", len(installed)).format(
                count=len(installed), size=GLib.format_size(size)
            )
            if installed
            else ""
        )

    def close(self) -> None:
        """Stop following the manager once the dialog is gone."""
        self._disconnect()
