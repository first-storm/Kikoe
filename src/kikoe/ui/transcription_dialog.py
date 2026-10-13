"""Choose the model and options the next recording or import is transcribed with."""

from collections.abc import Callable, Sequence

from gi.repository import Adw, Gtk

from kikoe.core.choice import ModelChoice, RequestedFeatures
from kikoe.i18n import _
from kikoe.ui.labels import capability_summary, transcription_mode
from kikoe.ui.options_group import OptionsGroup
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("transcription_dialog"))
class TranscriptionDialog(Adw.Dialog):
    """Changes apply at once, like preferences, so there is no Save button."""

    __gtype_name__ = "KikoeTranscriptionDialog"

    models_box: Gtk.Box = Gtk.Template.Child()
    options_slot: Gtk.Box = Gtk.Template.Child()

    def __init__(
        self,
        choices: Sequence[ModelChoice],
        current: ModelChoice | None,
        wanted: RequestedFeatures,
        on_change: Callable[[ModelChoice, RequestedFeatures], None],
    ) -> None:
        super().__init__()
        self._choice = current
        self._wanted = wanted
        self._on_change = on_change
        self._options = OptionsGroup()
        self._options.connect("changed", self._on_options_group_changed)
        self.options_slot.append(self._options)
        self._radio_anchor = Gtk.CheckButton()
        """Never shown; a lone model still needs a group to look like a radio, not a check."""
        self._current_radio: Gtk.CheckButton | None = None
        self._add_model_groups(choices)
        self._show_options()
        if self._current_radio is not None:
            self.set_focus(self._current_radio)

    def _add_model_groups(self, choices: Sequence[ModelChoice]) -> None:
        """One group per provider, titled with its name."""
        if not choices:
            self.models_box.append(
                Adw.PreferencesGroup(
                    title=_("Model"),
                    description=_("Add a provider and a model to start transcribing."),
                )
            )
            return
        groups: dict[str, Adw.PreferencesGroup] = {}
        for choice in choices:
            provider = choice.provider
            if provider.provider_id not in groups:
                groups[provider.provider_id] = Adw.PreferencesGroup(title=provider.name)
                self.models_box.append(groups[provider.provider_id])
            groups[provider.provider_id].add(self._create_model_row(choice))

    def _create_model_row(self, choice: ModelChoice) -> Adw.ActionRow:
        radio = Gtk.CheckButton(
            valign=Gtk.Align.CENTER,
            active=self._choice is not None and choice.key == self._choice.key,
        )
        if radio.get_active():
            self._current_radio = radio
        radio.set_group(self._radio_anchor)
        radio.connect("toggled", self._on_model_radio_toggled, choice)
        row = Adw.ActionRow(
            title=choice.model.display_name,
            subtitle=capability_summary(choice.model.capabilities),
            use_markup=False,
            activatable_widget=radio,
        )
        row.add_prefix(radio)
        return row

    def _on_model_radio_toggled(self, radio: Gtk.CheckButton, choice: ModelChoice) -> None:
        if not radio.get_active():
            return
        self._choice = choice
        self._show_options()
        self._on_change(choice, self._wanted)

    def _show_options(self) -> None:
        choice = self._choice
        self.options_slot.set_visible(choice is not None)
        if choice is None:
            return
        capabilities = choice.model.capabilities
        self._options.set_description(transcription_mode(capabilities))
        self._options.set_options(capabilities.recording, self._wanted)

    def _on_options_group_changed(self, group: OptionsGroup) -> None:
        self._wanted = group.wanted
        if self._choice is not None:
            self._on_change(self._choice, self._wanted)

    @Gtk.Template.Callback()
    def _on_manage_row_activated(self, _row: Adw.ButtonRow) -> None:
        # Close first so Preferences is not stacked on a dialog whose models it may change.
        window = self.get_root()
        self.close()
        if isinstance(window, Gtk.Widget):
            window.activate_action("app.manage-providers", None)
