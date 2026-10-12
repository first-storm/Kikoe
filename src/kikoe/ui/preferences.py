"""Preferences dialog. The Providers page lists providers and opens ``ProviderPage``."""

import asyncio
from dataclasses import replace

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.choice import ModelChoice, available_models
from kikoe.core.models import Provider
from kikoe.core.preferences import valid_language
from kikoe.i18n import _, ngettext
from kikoe.paths import data_dir, log_dir
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.items import ProviderItem
from kikoe.ui.labels import api_label
from kikoe.ui.provider_page import ProviderPage
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("preferences"))
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "KikoePreferencesDialog"

    providers_list: Gtk.ListBox = Gtk.Template.Child()
    default_model_row: Adw.ComboRow = Gtk.Template.Child()
    language_row: Adw.EntryRow = Gtk.Template.Child()
    language_error: Gtk.Label = Gtk.Template.Child()
    data_row: Adw.ActionRow = Gtk.Template.Child()
    log_row: Adw.ActionRow = Gtk.Template.Child()

    def __init__(self, repository: ProviderRepository) -> None:
        super().__init__()
        self._repository = repository
        self._updating = False
        self._shown_language: str | None = None
        self._choices: tuple[ModelChoice, ...] = ()
        self._disconnect_preferences = repository.connect_preferences_changed(
            self._refresh_defaults
        )
        run_task(self._show_paths(), self)
        self._providers: Gio.ListStore[ProviderItem] = Gio.ListStore(item_type=ProviderItem)
        self.providers_list.bind_model(self._providers, self._create_provider_row)
        self._disconnect = repository.connect_changed(self._refresh_providers)
        self._refresh_providers()

    async def _show_paths(self) -> None:
        data, logs = await asyncio.gather(asyncio.to_thread(data_dir), asyncio.to_thread(log_dir))
        self.data_row.set_subtitle(str(data))
        self.log_row.set_subtitle(str(logs))

    def _refresh_defaults(self) -> None:
        self._updating = True
        try:
            self._choices = available_models(self._repository.providers)
            names = [
                _("No Default Model"),
                *[
                    _("{provider} · {model}").format(
                        provider=c.provider.name, model=c.model.display_name
                    )
                    for c in self._choices
                ],
            ]
            self.default_model_row.set_model(Gtk.StringList.new(names))
            selected = next(
                (
                    i + 1
                    for i, c in enumerate(self._choices)
                    if c.key == self._repository.preferences.default_model
                ),
                0,
            )
            self.default_model_row.set_selected(selected)
            language = self._repository.preferences.language
            if language != self._shown_language:
                self.language_row.set_text(language or "")
                self._shown_language = language
                self.language_row.remove_css_class("error")
                self.language_error.set_visible(False)
                self.language_row.update_state(
                    [Gtk.AccessibleState.INVALID], [int(Gtk.AccessibleInvalidState.FALSE)]
                )
        finally:
            self._updating = False

    @Gtk.Template.Callback()
    def _on_default_model_row_notify_selected(
        self, _row: Adw.ComboRow, _spec: GObject.ParamSpec
    ) -> None:
        if self._updating:
            return
        index = self.default_model_row.get_selected()
        if index > len(self._choices):
            return
        key = self._choices[index - 1].key if index else None
        run_task(
            self._repository.update_preferences(
                lambda p: replace(p, default_model=key, recent_model=key)
            ),
            self,
        )

    @Gtk.Template.Callback()
    def _on_language_row_apply(self, _row: Adw.EntryRow) -> None:
        language = self.language_row.get_text().strip()
        if language and not valid_language(language):
            self.language_row.add_css_class("error")
            self.language_error.set_visible(True)
            self.language_row.update_state(
                [Gtk.AccessibleState.INVALID], [int(Gtk.AccessibleInvalidState.TRUE)]
            )
            self.language_row.set_tooltip_text(_("Enter a language code such as zh, en, or ja."))
            return
        self.language_row.remove_css_class("error")
        self.language_error.set_visible(False)
        self.language_row.update_state(
            [Gtk.AccessibleState.INVALID], [int(Gtk.AccessibleInvalidState.FALSE)]
        )
        run_task(
            self._repository.update_preferences(lambda p: replace(p, language=language or None)),
            self,
        )

    def start_add_provider(self) -> None:
        self.push_subpage(ProviderPage(self._repository))

    def _refresh_providers(self) -> None:
        items = [ProviderItem(p) for p in self._repository.providers]
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
        row.add_prefix(Adw.Avatar(size=40, text=provider.name, show_initials=True))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))

        def open_provider(_row: Adw.ActionRow) -> None:
            self.push_subpage(ProviderPage(self._repository, provider.provider_id))

        row.connect("activated", open_provider)
        return row

    @Gtk.Template.Callback()
    def _on_add_provider_button_clicked(self, _button: Gtk.Button) -> None:
        self.start_add_provider()

    @Gtk.Template.Callback()
    def _on_dialog_closed(self, _dialog: Adw.Dialog) -> None:
        self._disconnect()
        self._disconnect_preferences()


def _provider_subtitle(provider: Provider) -> str:
    count = len(provider.models)
    return ngettext("{api} API · {count} model", "{api} API · {count} models", count).format(
        api=api_label(provider.api), count=count
    )
