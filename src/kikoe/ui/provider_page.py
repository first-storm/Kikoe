"""Subpage of the preferences dialog to create a provider or edit one and its models.

A new provider is created with the Add button. An existing one is saved as soon as a
field is applied (Enter or the row's apply button) or the API type changes, as is usual
in GNOME preferences.
"""

import asyncio

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.models import ApiKind, ModelSpec, Provider, UploadSettings
from kikoe.core.upload import MAX_CHUNK_SECONDS, MAX_PARALLELISM, upload_policy
from kikoe.core.validation import DEFAULT_BASE_URLS, effective_base_url, validate_provider
from kikoe.i18n import _
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.items import ModelItem
from kikoe.ui.labels import capability_summary, provider_error_message
from kikoe.ui.model_dialog import ModelDialog
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file

_API_ORDER = tuple(ApiKind)
"""Matches the order of the strings in provider_page.blp."""


@Gtk.Template(filename=ui_file("provider_page"))
class ProviderPage(Adw.NavigationPage):
    __gtype_name__ = "KikoeProviderPage"

    add_button: Gtk.Button = Gtk.Template.Child()
    name_row: Adw.EntryRow = Gtk.Template.Child()
    api_row: Adw.ComboRow = Gtk.Template.Child()
    base_url_row: Adw.EntryRow = Gtk.Template.Child()
    api_key_row: Adw.PasswordEntryRow = Gtk.Template.Child()
    error_label: Gtk.Label = Gtk.Template.Child()
    parallel_row: Adw.SpinRow = Gtk.Template.Child()
    chunk_length_row: Adw.SpinRow = Gtk.Template.Child()
    chunk_size_row: Adw.SpinRow = Gtk.Template.Child()
    upload_format_row: Adw.ComboRow = Gtk.Template.Child()
    models_group: Adw.PreferencesGroup = Gtk.Template.Child()
    models_list: Gtk.ListBox = Gtk.Template.Child()
    remove_group: Adw.PreferencesGroup = Gtk.Template.Child()

    _populating = True
    """True until the fields are filled, so template signals fired on load are ignored."""

    def __init__(self, repository: ProviderRepository, provider_id: str | None = None) -> None:
        super().__init__()
        self._repository = repository
        self._save_lock = asyncio.Lock()
        self._provider_id = provider_id
        self._models: Gio.ListStore[ModelItem] = Gio.ListStore(item_type=ModelItem)
        self.models_list.bind_model(self._models, self._create_model_row)
        self._disconnect = lambda: None
        self._key_loaded = provider_id is None
        """Saving waits for the stored key, so an empty field cannot erase it by accident."""
        provider = repository.find(provider_id) if provider_id else None
        self._upload_api = provider.api if provider else _API_ORDER[self.api_row.get_selected()]
        self._show_upload(provider.upload if provider else UploadSettings())
        if provider is not None:
            self._show_provider(provider)
            self._disconnect = repository.connect_changed(self._refresh_models)
            run_task(self._load_api_key(provider.provider_id), self)
        self._populating = False
        self._on_api_row_notify_selected(self.api_row, None)

    def _show_provider(self, provider: Provider) -> None:
        self.set_title(provider.name)
        self.name_row.set_text(provider.name)
        self.api_row.set_selected(_API_ORDER.index(provider.api))
        self.base_url_row.set_text(provider.base_url)
        self.models_group.set_visible(True)
        self.remove_group.set_visible(True)
        self.add_button.set_visible(False)
        for row in (self.name_row, self.base_url_row, self.api_key_row):
            row.set_show_apply_button(True)
        self.api_key_row.set_sensitive(False)
        self.api_row.set_sensitive(False)
        self._refresh_models()

    async def _load_api_key(self, provider_id: str) -> None:
        self.api_key_row.set_text(await self._repository.get_api_key(provider_id))
        self.api_key_row.set_sensitive(True)
        self.api_row.set_sensitive(True)
        self._key_loaded = True

    def _refresh_models(self) -> None:
        provider = self._repository.find(self._provider_id) if self._provider_id else None
        if provider is None:
            return
        self._models.splice(0, self._models.get_n_items(), [ModelItem(m) for m in provider.models])

    def _create_model_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, ModelItem)
        model = item.model
        row = Adw.ActionRow(
            title=model.display_name,
            subtitle=capability_summary(model.capabilities),
            use_markup=False,
            activatable=True,
        )
        edit_button = Gtk.Button(
            icon_name="document-edit-symbolic",
            tooltip_text=_("Edit Model"),
            valign=Gtk.Align.CENTER,
            css_classes=["flat"],
        )
        edit_button.update_property([Gtk.AccessibleProperty.LABEL], [_("Edit Model")])

        def edit(_widget: Gtk.Widget) -> None:
            self._edit_model(model)

        edit_button.connect("clicked", edit)
        row.add_suffix(edit_button)
        row.connect("activated", edit)
        return row

    def _edit_model(self, model: ModelSpec) -> None:
        if self._provider_id:
            ModelDialog(self._repository, self._provider_id, model).present(self)

    @Gtk.Template.Callback()
    def _on_page_hidden(self, _page: Adw.NavigationPage) -> None:
        self._disconnect()

    @Gtk.Template.Callback()
    def _on_api_row_notify_selected(
        self, _row: Adw.ComboRow, _pspec: GObject.ParamSpec | None
    ) -> None:
        """Offer the default endpoint of hosted APIs, unless the user typed their own URL."""
        if self._populating:
            return
        api = _API_ORDER[self.api_row.get_selected()]
        if api != self._upload_api:
            self._upload_api = api
            self._show_upload(UploadSettings())
        current = self.base_url_row.get_text().strip()
        if not current or current in DEFAULT_BASE_URLS.values():
            self.base_url_row.set_text(DEFAULT_BASE_URLS.get(api, ""))
        self._apply_changes()

    @Gtk.Template.Callback()
    def _on_row_apply(self, _row: Adw.EntryRow) -> None:
        self._apply_changes()

    @Gtk.Template.Callback()
    def _on_add_button_clicked(self, _button: Gtk.Button) -> None:
        fields = self._valid_fields()
        if fields is not None:
            # Disabled while saving so a double click cannot add the same provider twice.
            self.add_button.set_sensitive(False)
            run_task(self._add(*fields), self)

    def _apply_changes(self) -> None:
        """Save an existing provider right away; a new one waits for the Add button."""
        if self._provider_id is None or not self._key_loaded:
            return
        fields = self._valid_fields()
        if fields is not None:
            run_task(self._update(self._provider_id, *fields, self._upload_settings()), self)

    def _valid_fields(self) -> tuple[str, ApiKind, str, str] | None:
        """The entered connection details, or None after showing why they are invalid."""
        name = self.name_row.get_text()
        api = _API_ORDER[self.api_row.get_selected()]
        base_url = self.base_url_row.get_text()
        error = validate_provider(name, api, base_url)
        self.error_label.set_visible(error is not None)
        if error is not None:
            self.error_label.set_label(provider_error_message(error))
            return None
        api_key = self.api_key_row.get_text().strip()
        return name, api, effective_base_url(api, base_url), api_key

    async def _add(self, name: str, api: ApiKind, base_url: str, api_key: str) -> None:
        try:
            await self._repository.add_provider(
                name, api, base_url, api_key, self._upload_settings()
            )
        finally:
            self.add_button.set_sensitive(True)
        self._pop()

    async def _update(
        self,
        provider_id: str,
        name: str,
        api: ApiKind,
        base_url: str,
        api_key: str,
        upload: UploadSettings,
    ) -> None:
        async with self._save_lock:
            await self._repository.update_provider(
                provider_id,
                name,
                api,
                base_url,
                api_key,
                upload=upload,
            )
            self.set_title(name.strip())

    def _show_upload(self, settings: UploadSettings) -> None:
        populating = self._populating
        self._populating = True
        policy = upload_policy(self._upload_api)
        for row in (self.parallel_row, self.chunk_length_row, self.chunk_size_row):
            row.get_adjustment().set_step_increment(1)
            row.get_adjustment().set_page_increment(1 if row is self.parallel_row else 10)
        self.parallel_row.get_adjustment().set_upper(MAX_PARALLELISM)
        self.parallel_row.set_value(settings.parallelism)
        self.chunk_length_row.get_adjustment().set_upper((policy.seconds or MAX_CHUNK_SECONDS) / 60)
        self.chunk_length_row.set_value((settings.chunk_seconds or policy.seconds or 0) / 60)
        self.chunk_size_row.get_adjustment().set_upper(policy.bytes / 1_000_000)
        self.chunk_size_row.set_value((settings.chunk_bytes or policy.bytes) / 1_000_000)
        self.chunk_size_row.set_subtitle(
            _("Maximum {size:g} MB per chunk").format(size=policy.bytes / 1_000_000)
        )
        if self._upload_api == ApiKind.GEMINI:
            self.chunk_length_row.set_subtitle(
                _("Maximum 60 minutes, or 30 with timestamps or speaker detection")
            )
        else:
            self.chunk_length_row.set_subtitle(
                _("0 uses the provider limit; chunks also respect the size limit")
            )
        if self._upload_api == ApiKind.OPENAI_COMPATIBLE:
            self.chunk_size_row.set_subtitle(
                _("Defaults to 25 MB; your server may require smaller chunks")
            )
        self.upload_format_row.set_model(
            Gtk.StringList.new([format.value.upper() for format in policy.formats])
        )
        self.upload_format_row.set_selected(policy.formats.index(settings.format or policy.format))
        self.upload_format_row.set_sensitive(len(policy.formats) > 1)
        self._populating = populating

    def _upload_settings(self) -> UploadSettings:
        policy = upload_policy(self._upload_api)
        seconds = round(self.chunk_length_row.get_value() * 60)
        size = round(self.chunk_size_row.get_value() * 1_000_000)
        format = policy.formats[self.upload_format_row.get_selected()]
        return UploadSettings(
            round(self.parallel_row.get_value()),
            None if seconds == 0 or seconds == policy.seconds else seconds,
            None if size == policy.bytes else size,
            None if format == policy.format else format,
        )

    @Gtk.Template.Callback()
    def _on_upload_row_changed(self, _row: Adw.PreferencesRow, _pspec: GObject.ParamSpec) -> None:
        if self._populating or self._provider_id is None:
            return
        run_task(self._save_upload(self._upload_settings()), self)

    async def _save_upload(self, settings: UploadSettings) -> None:
        async with self._save_lock:
            if self._provider_id is not None:
                await self._repository.update_upload(self._provider_id, settings)

    @Gtk.Template.Callback()
    def _on_reset_upload_activated(self, _row: Adw.ButtonRow) -> None:
        self._show_upload(UploadSettings())
        if self._provider_id is not None:
            run_task(self._save_upload(UploadSettings()), self)

    @Gtk.Template.Callback()
    def _on_add_model_button_clicked(self, _button: Gtk.Button) -> None:
        if self._provider_id:
            ModelDialog(self._repository, self._provider_id).present(self)

    @Gtk.Template.Callback()
    def _on_remove_row_activated(self, _row: Adw.ButtonRow) -> None:
        dialog = Adw.AlertDialog(
            heading=_("Remove “{name}”?").format(name=self.name_row.get_text()),
            body=_("Its models and API key will be deleted."),
        )
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("remove", _("_Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_dialog_response)
        dialog.present(self)

    def _on_remove_dialog_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response == "remove" and self._provider_id:
            run_task(self._remove(self._provider_id), self)

    async def _remove(self, provider_id: str) -> None:
        await self._repository.remove_provider(provider_id)
        self._pop()

    def _pop(self) -> None:
        navigation = self.get_ancestor(Adw.NavigationView)
        if navigation is not None:
            navigation.pop()
