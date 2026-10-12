"""Bar below the note: model picker, feature chips, Import Audio, and import progress."""

from gi.repository import Adw, Gio, GObject, Gtk, Pango

from kikoe.core.choice import (
    ModelChoice,
    RequestedFeatures,
    available_models,
    resolve_choice,
)
from kikoe.core.models import ModelSpec
from kikoe.core.validation import Capability
from kikoe.i18n import _
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.items import ModelChoiceItem
from kikoe.ui.labels import capability_summary, chip_tooltip, transcription_hint
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("bottom_bar"))
class BottomBar(Adw.Bin):
    __gtype_name__ = "KikoeBottomBar"

    model_button: Gtk.MenuButton = Gtk.Template.Child()
    model_label: Gtk.Label = Gtk.Template.Child()
    provider_label: Gtk.Label = Gtk.Template.Child()
    timestamps_chip: Gtk.ToggleButton = Gtk.Template.Child()
    speakers_chip: Gtk.ToggleButton = Gtk.Template.Child()
    hint_label: Gtk.Label = Gtk.Template.Child()
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
        self._wanted = RequestedFeatures()
        """What the user asked for, kept while a model that lacks a feature is chosen."""
        self._updating = False
        self._models: Gio.ListStore[ModelChoiceItem] = Gio.ListStore(item_type=ModelChoiceItem)
        self.model_list.bind_model(self._models, self._create_model_row)
        self.model_list.set_header_func(self._update_header)

    @GObject.Signal
    def choice_changed(self) -> None:
        """The chosen model changed, or appeared or disappeared."""

    @property
    def choice(self) -> ModelChoice | None:
        return self._choice

    @property
    def requested_features(self) -> RequestedFeatures:
        """The chips' state, limited to what the chosen model supports."""
        if self._choice is None:
            return RequestedFeatures(timestamps=False, diarization=False)
        return self._wanted.allowed_by(self._choice.model.capabilities)

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
        for widget in (
            self.model_button,
            self.timestamps_chip,
            self.speakers_chip,
            self.hint_label,
            self.import_button,
        ):
            widget.set_visible(not busy)

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
            self.provider_label.set_label("")
            self.import_button.set_tooltip_text(_("Add a model in Preferences first"))
        else:
            self.model_label.set_label(choice.model.display_name)
            self.provider_label.set_label(choice.provider.name)
            self.import_button.set_tooltip_text(None)
        self._show_chips(choice.model if choice else None)
        self.emit("choice-changed")

    def _show_chips(self, model: ModelSpec | None) -> None:
        self.timestamps_chip.set_visible(model is not None)
        self.speakers_chip.set_visible(model is not None)
        self.hint_label.set_visible(model is not None)
        if model is None:
            return
        allowed = self._wanted.allowed_by(model.capabilities)
        chips = (
            (
                self.timestamps_chip,
                Capability.TIMESTAMPS,
                model.capabilities.timestamps,
                allowed.timestamps,
            ),
            (
                self.speakers_chip,
                Capability.DIARIZATION,
                model.capabilities.diarization,
                allowed.diarization,
            ),
        )
        self._updating = True
        try:
            for chip, capability, supported, active in chips:
                chip.set_sensitive(supported)
                chip.set_active(active)
                if supported:
                    chip.remove_css_class("unsupported")
                else:
                    chip.add_css_class("unsupported")
                chip.set_tooltip_text(
                    chip_tooltip(capability, model.display_name, supported, active)
                )
        finally:
            self._updating = False
        self.hint_label.set_label(transcription_hint(model.capabilities))

    @Gtk.Template.Callback()
    def _on_chip_toggled(self, _chip: Gtk.ToggleButton) -> None:
        if self._updating or self._choice is None:
            return
        caps = self._choice.model.capabilities
        self._wanted = RequestedFeatures(
            timestamps=self.timestamps_chip.get_active()
            if caps.timestamps
            else self._wanted.timestamps,
            diarization=self.speakers_chip.get_active()
            if caps.diarization
            else self._wanted.diarization,
        )
        self._show_chips(self._choice.model)

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
