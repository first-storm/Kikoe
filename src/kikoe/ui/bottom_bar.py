"""Bar below the note: the transcription model picker, Import Audio, and import progress."""

from gi.repository import Adw, Gio, GObject, Gtk, Pango

from kikoe.core.choice import ModelChoice, available_models, resolve_choice
from kikoe.i18n import _
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.items import ModelChoiceItem
from kikoe.ui.labels import capability_summary
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("bottom_bar"))
class BottomBar(Adw.Bin):
    __gtype_name__ = "KikoeBottomBar"

    model_button: Gtk.MenuButton = Gtk.Template.Child()
    model_label: Gtk.Label = Gtk.Template.Child()
    model_popover: Gtk.Popover = Gtk.Template.Child()
    model_list: Gtk.ListBox = Gtk.Template.Child()
    progress_box: Gtk.Box = Gtk.Template.Child()
    progress_label: Gtk.Label = Gtk.Template.Child()
    import_button: Gtk.Button = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self._repository: ProviderRepository | None = None
        self._choice: ModelChoice | None = None
        self._models: Gio.ListStore[ModelChoiceItem] = Gio.ListStore(item_type=ModelChoiceItem)
        self.model_list.bind_model(self._models, self._create_model_row)
        self.model_list.set_header_func(self._update_header)

    @GObject.Signal
    def choice_changed(self) -> None:
        """The chosen model changed, or appeared or disappeared."""

    @property
    def choice(self) -> ModelChoice | None:
        return self._choice

    def bind_providers(self, repository: ProviderRepository) -> None:
        self._repository = repository
        repository.connect_changed(self._refresh)
        self._refresh()

    def set_progress(self, message: str | None) -> None:
        """Show ``message`` with a spinner and a Cancel button, or the controls when None."""
        busy = message is not None
        self.progress_label.set_label(message or "")
        self.progress_box.set_visible(busy)
        self.cancel_button.set_visible(busy)
        self.model_button.set_visible(not busy)
        self.import_button.set_visible(not busy)

    def _refresh(self) -> None:
        providers = self._repository.providers if self._repository else ()
        wanted = self._choice.key if self._choice else None
        self._choice = resolve_choice(providers, wanted)
        self._models.splice(
            0,
            self._models.get_n_items(),
            [ModelChoiceItem(choice) for choice in available_models(providers)],
        )
        self._show_choice()

    def _show_choice(self) -> None:
        choice = self._choice
        if choice is None:
            self.model_label.set_label(_("No Model"))
            self.import_button.set_tooltip_text(_("Add a model in Preferences first"))
        else:
            self.model_label.set_label(
                _("{model} · {provider}").format(
                    model=choice.model.display_name, provider=choice.provider.name
                )
            )
            self.import_button.set_tooltip_text(None)
        self.emit("choice-changed")

    def _create_model_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, ModelChoiceItem)
        choice = item.choice
        title = Gtk.Label(
            label=choice.model.display_name, xalign=0, ellipsize=Pango.EllipsizeMode.END
        )
        subtitle = Gtk.Label(
            label=capability_summary(choice.model.capabilities),
            xalign=0,
            css_classes=["caption", "dim-label"],
        )
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        labels.append(title)
        labels.append(subtitle)
        check = Gtk.Image(
            icon_name="object-select-symbolic",
            opacity=1 if self._choice is not None and choice.key == self._choice.key else 0,
        )
        row = Gtk.Box(spacing=12, margin_top=3, margin_bottom=3)
        row.append(labels)
        row.append(check)
        return row

    def _update_header(self, row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
        """Group models under their provider's name."""
        current = self._models.get_item(row.get_index())
        previous = self._models.get_item(before.get_index()) if before is not None else None
        if current is None or (
            previous is not None
            and previous.choice.provider.provider_id == current.choice.provider.provider_id
        ):
            row.set_header(None)
            return
        row.set_header(
            Gtk.Label(
                label=current.choice.provider.name,
                xalign=0,
                margin_start=6,
                margin_top=6 if before is not None else 0,
                css_classes=["heading", "dim-label"],
            )
        )

    @Gtk.Template.Callback()
    def _on_model_list_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        item = self._models.get_item(row.get_index())
        self.model_popover.popdown()
        if item is None:
            return
        self._choice = item.choice
        # Rebuild the rows so the check mark moves.
        self._refresh()
