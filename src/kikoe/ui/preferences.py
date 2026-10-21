"""Preferences dialog. The Providers page lists providers and opens ``ProviderPage``."""

import asyncio
import logging
from dataclasses import replace

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.models import Provider
from kikoe.core.preferences import (
    MAX_REALTIME_DELAY_MS,
    MIN_REALTIME_DELAY_MS,
    Preferences,
    parse_context_bias,
    valid_context_bias,
    valid_language,
)
from kikoe.i18n import _, ngettext
from kikoe.media.devices import DeviceKind, list_devices
from kikoe.paths import data_dir, log_dir
from kikoe.services.local_models import LocalModelManager
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.guard import Guard
from kikoe.ui.items import ProviderItem
from kikoe.ui.labels import api_label
from kikoe.ui.local_models_page import LocalModelsPage
from kikoe.ui.provider_page import ProviderPage
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file

log = logging.getLogger(__name__)


@Gtk.Template(filename=ui_file("preferences"))
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "KikoePreferencesDialog"

    providers_list: Gtk.ListBox = Gtk.Template.Child()
    language_row: Adw.EntryRow = Gtk.Template.Child()
    language_error: Gtk.Label = Gtk.Template.Child()
    vocabulary_row: Adw.EntryRow = Gtk.Template.Child()
    vocabulary_error: Gtk.Label = Gtk.Template.Child()
    realtime_delay_row: Adw.SpinRow = Gtk.Template.Child()
    input_device_row: Adw.ComboRow = Gtk.Template.Child()
    output_device_row: Adw.ComboRow = Gtk.Template.Child()
    data_row: Adw.ActionRow = Gtk.Template.Child()
    log_row: Adw.ActionRow = Gtk.Template.Child()

    def __init__(
        self,
        repository: ProviderRepository,
        preferences: PreferencesRepository,
        local_models: LocalModelManager,
    ) -> None:
        super().__init__()
        self._local_page = LocalModelsPage(local_models)
        self.add(self._local_page)
        self._repository = repository
        self._preferences = preferences
        self._updating = Guard()
        self._shown_language: str | None = None
        self._shown_vocabulary: tuple[str, ...] | None = None
        # Blueprint rejects increments next to value, so the coarse steps are set here.
        self.realtime_delay_row.get_adjustment().configure(0, 0, MAX_REALTIME_DELAY_MS, 20, 200, 0)
        self._device_names: dict[DeviceKind, list[str]] = {"input": [], "output": []}
        self._disconnect_preferences = preferences.connect_changed(self._refresh_defaults)
        run_task(self._show_paths(), self)
        run_task(self._load_devices(), self)
        self._providers: Gio.ListStore[ProviderItem] = Gio.ListStore(item_type=ProviderItem)
        self.providers_list.bind_model(self._providers, self._create_provider_row)
        self._disconnect = repository.connect_changed(self._refresh_providers)
        self._refresh_providers()

    async def _show_paths(self) -> None:
        data, logs = await asyncio.gather(asyncio.to_thread(data_dir), asyncio.to_thread(log_dir))
        self.data_row.set_subtitle(str(data))
        self.log_row.set_subtitle(str(logs))

    async def _load_devices(self) -> None:
        for kind in ("input", "output"):
            try:
                devices = await list_devices(kind)
            except Exception:  # Missing PortAudio must not block the other preferences.
                log.exception("Could not list %s audio devices", kind)
                continue
            self._device_names[kind] = [device.name for device in devices]
        self._refresh_devices()

    def _device_row(self, kind: DeviceKind) -> Adw.ComboRow:
        return self.input_device_row if kind == "input" else self.output_device_row

    def _saved_device(self, kind: DeviceKind) -> str | None:
        preferences = self._preferences.current
        return preferences.input_device if kind == "input" else preferences.output_device

    def _device_entries(self, kind: DeviceKind) -> list[str]:
        """Names behind the rows after "System Default"; a missing saved device stays listed."""
        names = list(self._device_names[kind])
        saved = self._saved_device(kind)
        if saved is not None and saved not in names:
            names.append(saved)
        return names

    def _refresh_devices(self) -> None:
        with self._updating:
            for kind in ("input", "output"):
                names = self._device_names[kind]
                entries = self._device_entries(kind)
                labels = [
                    _("{device} (Unavailable)").format(device=name) if name not in names else name
                    for name in entries
                ]
                row = self._device_row(kind)
                row.set_model(Gtk.StringList.new([_("System Default"), *labels]))
                saved = self._saved_device(kind)
                row.set_selected(entries.index(saved) + 1 if saved in entries else 0)

    def _select_device(self, kind: DeviceKind) -> None:
        if self._updating.active:
            return
        index = self._device_row(kind).get_selected()
        entries = self._device_entries(kind)
        if index > len(entries):
            return
        name = entries[index - 1] if index else None

        def change(preferences: Preferences) -> Preferences:
            if kind == "input":
                return replace(preferences, input_device=name)
            return replace(preferences, output_device=name)

        run_task(self._preferences.update(change), self)

    @Gtk.Template.Callback()
    def _on_input_device_row_notify_selected(
        self, _row: Adw.ComboRow, _spec: GObject.ParamSpec
    ) -> None:
        self._select_device("input")

    @Gtk.Template.Callback()
    def _on_output_device_row_notify_selected(
        self, _row: Adw.ComboRow, _spec: GObject.ParamSpec
    ) -> None:
        self._select_device("output")

    def _refresh_defaults(self) -> None:
        with self._updating:
            self._refresh_devices()
            language = self._preferences.current.language
            if language != self._shown_language:
                self.language_row.set_text(language or "")
                self._shown_language = language
                self._show_language_error(False)
            vocabulary = self._preferences.current.context_bias
            if vocabulary != self._shown_vocabulary:
                self.vocabulary_row.set_text(", ".join(vocabulary))
                self._shown_vocabulary = vocabulary
                self.vocabulary_error.set_visible(False)
            self.realtime_delay_row.set_value(self._preferences.current.realtime_delay_ms or 0)

    @Gtk.Template.Callback()
    def _on_vocabulary_row_apply(self, _row: Adw.EntryRow) -> None:
        terms = parse_context_bias(self.vocabulary_row.get_text())
        invalid = not valid_context_bias(terms)
        self.vocabulary_error.set_visible(invalid)
        if invalid:
            return
        run_task(self._preferences.update(lambda p: replace(p, context_bias=terms)), self)

    @Gtk.Template.Callback()
    def _on_realtime_delay_row_notify_value(
        self, _row: Adw.SpinRow, _spec: GObject.ParamSpec
    ) -> None:
        if self._updating.active:
            return
        value = round(self.realtime_delay_row.get_value())
        delay = max(value, MIN_REALTIME_DELAY_MS) if value else None
        if delay is not None:
            delay = min(delay, MAX_REALTIME_DELAY_MS)
        if delay != value and delay is not None:
            with self._updating:
                self.realtime_delay_row.set_value(delay)
        run_task(self._preferences.update(lambda p: replace(p, realtime_delay_ms=delay)), self)

    @Gtk.Template.Callback()
    def _on_language_row_apply(self, _row: Adw.EntryRow) -> None:
        language = self.language_row.get_text().strip()
        invalid = bool(language) and not valid_language(language)
        self._show_language_error(invalid)
        if invalid:
            return
        run_task(
            self._preferences.update(lambda p: replace(p, language=language or None)),
            self,
        )

    def start_add_provider(self) -> None:
        self.push_subpage(ProviderPage(self._repository))

    def _refresh_providers(self) -> None:
        items = [ProviderItem(p) for p in self._repository.configured]
        self._providers.splice(0, self._providers.get_n_items(), items)
        self._refresh_defaults()

    def _create_provider_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, ProviderItem)
        provider = item.provider
        row = Adw.ActionRow(
            title=provider.name,
            subtitle=_provider_subtitle(provider),
            use_markup=False,
            activatable=True,
        )
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))

        def open_provider(_row: Adw.ActionRow) -> None:
            self.push_subpage(ProviderPage(self._repository, provider.provider_id))

        row.connect("activated", open_provider)
        return row

    def _show_language_error(self, invalid: bool) -> None:
        if invalid:
            self.language_row.add_css_class("error")
        else:
            self.language_row.remove_css_class("error")
        self.language_error.set_visible(invalid)
        self.language_row.update_state(
            [Gtk.AccessibleState.INVALID],
            [int(Gtk.AccessibleInvalidState.TRUE if invalid else Gtk.AccessibleInvalidState.FALSE)],
        )
        self.language_row.set_tooltip_text(
            _("Enter a language code such as zh, en, or ja.") if invalid else None
        )

    @Gtk.Template.Callback()
    def _on_add_provider_button_clicked(self, _button: Gtk.Button) -> None:
        self.start_add_provider()

    @Gtk.Template.Callback()
    def _on_dialog_closed(self, _dialog: Adw.Dialog) -> None:
        self._disconnect()
        self._disconnect_preferences()
        self._local_page.close()


def _provider_subtitle(provider: Provider) -> str:
    count = len(provider.models)
    return ngettext("{api} API · {count} model", "{api} API · {count} models", count).format(
        api=api_label(provider.api), count=count
    )
