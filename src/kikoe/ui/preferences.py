"""Preferences dialog. The Providers page lists providers and opens ``ProviderPage``."""

from gi.repository import Adw, Gio, GObject, Gtk

from kikoe.core.models import Provider
from kikoe.i18n import ngettext
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.items import ProviderItem
from kikoe.ui.labels import api_label
from kikoe.ui.provider_page import ProviderPage
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("preferences"))
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "KikoePreferencesDialog"

    providers_list: Gtk.ListBox = Gtk.Template.Child()

    def __init__(self, repository: ProviderRepository) -> None:
        super().__init__()
        self._repository = repository
        self._providers: Gio.ListStore[ProviderItem] = Gio.ListStore(item_type=ProviderItem)
        self.providers_list.bind_model(self._providers, self._create_provider_row)
        self._disconnect = repository.connect_changed(self._refresh_providers)
        self._refresh_providers()

    def start_add_provider(self) -> None:
        self.push_subpage(ProviderPage(self._repository))

    def _refresh_providers(self) -> None:
        items = [ProviderItem(p) for p in self._repository.providers]
        self._providers.splice(0, self._providers.get_n_items(), items)

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


def _provider_subtitle(provider: Provider) -> str:
    count = len(provider.models)
    return ngettext("{api} API · {count} model", "{api} API · {count} models", count).format(
        api=api_label(provider.api), count=count
    )
