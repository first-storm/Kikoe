"""Dialog for adding a model to a provider or editing an existing one."""

from gi.repository import Adw, Gtk

from kikoe.core.models import Capabilities, ModelSpec
from kikoe.core.transcription import model_support
from kikoe.core.validation import validate_model
from kikoe.i18n import _
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.labels import model_error_message
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("model_dialog"))
class ModelDialog(Adw.Dialog):
    __gtype_name__ = "KikoeModelDialog"

    confirm_button: Gtk.Button = Gtk.Template.Child()
    model_id_row: Adw.EntryRow = Gtk.Template.Child()
    display_name_row: Adw.EntryRow = Gtk.Template.Child()
    realtime_check: Gtk.CheckButton = Gtk.Template.Child()
    timestamps_check: Gtk.CheckButton = Gtk.Template.Child()
    diarization_check: Gtk.CheckButton = Gtk.Template.Child()
    error_label: Gtk.Label = Gtk.Template.Child()
    remove_group: Adw.PreferencesGroup = Gtk.Template.Child()

    def __init__(
        self, repository: ProviderRepository, provider_id: str, model: ModelSpec | None = None
    ) -> None:
        super().__init__()
        self._repository = repository
        self._provider_id = provider_id
        self._model = model
        if model is None:
            self._update_capabilities()
            return
        self.set_title(_("Edit Model"))
        self.confirm_button.set_label(_("_Save"))
        self.remove_group.set_visible(True)
        self.model_id_row.set_text(model.model_id)
        self.display_name_row.set_text(model.display_name)
        self.realtime_check.set_active(model.capabilities.realtime)
        self.timestamps_check.set_active(model.capabilities.timestamps)
        self.diarization_check.set_active(model.capabilities.diarization)
        self._update_capabilities()

    @Gtk.Template.Callback()
    def _on_model_id_row_changed(self, _row: Adw.EntryRow) -> None:
        self._update_capabilities()

    def _update_capabilities(self) -> None:
        provider = self._repository.find(self._provider_id)
        if provider is None:
            return
        model = ModelSpec(self.model_id_row.get_text().strip(), "", Capabilities(True, True, True))
        support = model_support(provider.api, model)
        batch = support.batch if support.file else Capabilities()
        for check, available in (
            (self.realtime_check, support.recording.realtime),
            (self.timestamps_check, batch.timestamps or support.recording.timestamps),
            (self.diarization_check, batch.diarization or support.recording.diarization),
        ):
            check.set_sensitive(available)
            check.set_tooltip_text(
                None if available else _("This model does not support this option.")
            )
            if not available:
                check.set_active(False)
        if not support.file:
            self.realtime_check.set_active(True)
            self.realtime_check.set_sensitive(False)

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    @Gtk.Template.Callback()
    def _on_confirm_button_clicked(self, _button: Gtk.Button) -> None:
        provider = self._repository.find(self._provider_id)
        if provider is None:
            self.close()
            return
        model_id = self.model_id_row.get_text().strip()
        editing_id = self._model.model_id if self._model else None
        existing_ids = [m.model_id for m in provider.models]
        error = validate_model(model_id, existing_ids, editing_id)
        self.model_id_row.set_css_classes(["error"] if error else [])
        self.error_label.set_visible(error is not None)
        if error is not None:
            self.error_label.set_label(model_error_message(error))
            return
        spec = ModelSpec(
            model_id,
            self.display_name_row.get_text().strip() or model_id,
            Capabilities(
                realtime=self.realtime_check.get_active(),
                timestamps=self.timestamps_check.get_active(),
                diarization=self.diarization_check.get_active(),
            ),
        )
        run_task(self._save(spec, editing_id), self)

    @Gtk.Template.Callback()
    def _on_remove_row_activated(self, _row: Adw.ButtonRow) -> None:
        if self._model is not None:
            run_task(self._remove(self._model.model_id), self)

    async def _save(self, spec: ModelSpec, replacing_id: str | None) -> None:
        await self._repository.save_model(self._provider_id, spec, replacing_id)
        self.close()

    async def _remove(self, model_id: str) -> None:
        await self._repository.remove_model(self._provider_id, model_id)
        self.close()
