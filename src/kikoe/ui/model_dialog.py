"""Dialog for adding a model to a provider or editing an existing one."""

from gi.repository import Adw, GObject, Gtk

from kikoe.core.model_catalog import ModelPreset, ModePreset, Support
from kikoe.core.models import Capabilities, ModeCapabilities, ModelSpec
from kikoe.core.validation import ModelError, validate_model
from kikoe.i18n import _
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.dialogs import confirm_discard
from kikoe.ui.guard import Guard
from kikoe.ui.labels import model_error_message
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("model_dialog"))
class ModelDialog(Adw.Dialog):
    """A new model takes the catalog preset for its ID until the user changes an option."""

    __gtype_name__ = "KikoeModelDialog"

    confirm_button: Gtk.Button = Gtk.Template.Child()
    model_id_row: Adw.EntryRow = Gtk.Template.Child()
    display_name_row: Adw.EntryRow = Gtk.Template.Child()
    file_row: Adw.ExpanderRow = Gtk.Template.Child()
    file_timestamps_check: Gtk.CheckButton = Gtk.Template.Child()
    file_speakers_check: Gtk.CheckButton = Gtk.Template.Child()
    file_streaming_check: Gtk.CheckButton = Gtk.Template.Child()
    realtime_row: Adw.ExpanderRow = Gtk.Template.Child()
    realtime_timestamps_check: Gtk.CheckButton = Gtk.Template.Child()
    realtime_speakers_check: Gtk.CheckButton = Gtk.Template.Child()
    error_label: Gtk.Label = Gtk.Template.Child()
    remove_group: Adw.PreferencesGroup = Gtk.Template.Child()

    def __init__(
        self, repository: ProviderRepository, provider_id: str, model: ModelSpec | None = None
    ) -> None:
        super().__init__()
        self._repository = repository
        self._provider_id = provider_id
        self._model = model
        self._updating = Guard()
        self._touched = model is not None
        """Saved options are never replaced by a preset, even when the ID is edited."""
        self._subtitles = {
            row: row.get_subtitle() or "" for row in (self.file_row, self.realtime_row)
        }
        self._checks = {
            self.file_row: (
                (self.file_timestamps_check, "timestamps"),
                (self.file_speakers_check, "diarization"),
                (self.file_streaming_check, "streaming"),
            ),
            self.realtime_row: (
                (self.realtime_timestamps_check, "timestamps"),
                (self.realtime_speakers_check, "diarization"),
            ),
        }
        """Each mode's check buttons and the `ModeCapabilities`/`ModePreset` field they set."""
        if model is not None:
            self.set_title(_("Edit Model"))
            self.confirm_button.set_label(_("_Save"))
            self.remove_group.set_visible(True)
            self.model_id_row.set_text(model.model_id)
            self.display_name_row.set_text(model.display_name)
            self._show_capabilities(model.capabilities)
        self._update_capabilities()
        self._guard_unsaved_input()

    def _capabilities(self) -> Capabilities:
        return Capabilities(file=self._mode(self.file_row), realtime=self._mode(self.realtime_row))

    def _mode(self, row: Adw.ExpanderRow) -> ModeCapabilities | None:
        if not row.get_enable_expansion():
            return None
        return ModeCapabilities(**{field: check.get_active() for check, field in self._checks[row]})

    def _show_capabilities(self, capabilities: Capabilities) -> None:
        with self._updating:
            for row, mode in (
                (self.file_row, capabilities.file),
                (self.realtime_row, capabilities.realtime),
            ):
                row.set_enable_expansion(mode is not None)
                for check, field in self._checks[row]:
                    active: bool = getattr(mode or ModeCapabilities(), field)
                    check.set_active(active)

    def _fields(self) -> tuple[str, str, Capabilities]:
        return (
            self.model_id_row.get_text(),
            self.display_name_row.get_text(),
            self._capabilities(),
        )

    def _guard_unsaved_input(self) -> None:
        initial = self._fields()
        confirm_discard(self, lambda: self._fields() != initial)

    @Gtk.Template.Callback()
    def _on_model_id_row_changed(self, _row: Adw.EntryRow) -> None:
        self._update_capabilities()

    @Gtk.Template.Callback()
    def _on_option_changed(self, _widget: Gtk.Widget, _spec: GObject.ParamSpec) -> None:
        if not self._updating.active:
            self._touched = True

    def _preset(self) -> ModelPreset | None:
        provider = self._repository.find(self._provider_id)
        if provider is None:
            return None
        model_id = self.model_id_row.get_text().strip()
        return self._repository.catalog.preset(provider.api, model_id)

    def _update_capabilities(self) -> None:
        preset = self._preset()
        if preset is None:
            return
        if self._touched:
            current = self._capabilities()
            self._show_capabilities(
                Capabilities(
                    file=preset.file.clamp(current.file),
                    realtime=preset.realtime.clamp(current.realtime),
                )
            )
        else:
            self._show_capabilities(preset.defaults())
        with self._updating:
            self._limit_mode(self.file_row, preset.file, preset.realtime)
            self._limit_mode(self.realtime_row, preset.realtime, preset.file)

    def _limit_mode(self, row: Adw.ExpanderRow, mode: ModePreset, other: ModePreset) -> None:
        """Disable what the endpoint rejects; a mode the model cannot lack is always on."""
        for check, field in self._checks[row]:
            support: Support = getattr(mode, field)
            available = support.allowed
            # Dim the whole row, so its title and description read as unavailable too.
            row_widget = check.get_ancestor(Adw.ActionRow) or check
            row_widget.set_sensitive(available)
            row_widget.set_tooltip_text(
                None if available else _("This model does not support this option.")
            )
            if not available:
                check.set_active(False)
        supported = mode.available is not Support.UNSUPPORTED
        required = other.available is Support.UNSUPPORTED
        row.set_sensitive(supported)
        row.set_show_enable_switch(not required)
        if required:
            row.set_enable_expansion(True)
        if not supported:
            row.set_enable_expansion(False)
            row.set_expanded(False)
        row.set_subtitle(
            self._subtitles[row] if supported else _("This model does not support this option.")
        )

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.force_close()

    @Gtk.Template.Callback()
    def _on_confirm_button_clicked(self, _button: Gtk.Button) -> None:
        provider = self._repository.find(self._provider_id)
        if provider is None:
            self.force_close()
            return
        model_id = self.model_id_row.get_text().strip()
        editing_id = self._model.model_id if self._model else None
        existing_ids = [m.model_id for m in provider.models]
        capabilities = self._capabilities()
        error = validate_model(model_id, existing_ids, editing_id, capabilities)
        id_error = error is not None and error is not ModelError.NO_MODE
        self.model_id_row.set_css_classes(["error"] if id_error else [])
        self.error_label.set_visible(error is not None)
        if error is not None:
            self.error_label.set_label(model_error_message(error))
            return
        spec = ModelSpec(
            model_id, self.display_name_row.get_text().strip() or model_id, capabilities
        )
        # Validation saw the models before this save lands, so a second click must wait.
        self.confirm_button.set_sensitive(False)
        run_task(self._save(spec, editing_id), self)

    @Gtk.Template.Callback()
    def _on_remove_row_activated(self, _row: Adw.ButtonRow) -> None:
        if self._model is not None:
            run_task(self._remove(self._model.model_id), self)

    async def _save(self, spec: ModelSpec, replacing_id: str | None) -> None:
        try:
            await self._repository.save_model(self._provider_id, spec, replacing_id)
        finally:
            self.confirm_button.set_sensitive(True)
        self.force_close()

    async def _remove(self, model_id: str) -> None:
        await self._repository.remove_model(self._provider_id, model_id)
        self.force_close()
