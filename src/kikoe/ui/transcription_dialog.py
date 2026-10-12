"""Choose the model and options the next recording or import is transcribed with."""

from collections.abc import Callable, Sequence
from dataclasses import replace

from gi.repository import Adw, GObject, Gtk

from kikoe.core.choice import ModelChoice, RequestedFeatures, normalize_granularities
from kikoe.core.models import ApiKind, TimestampGranularity
from kikoe.core.transcription import model_support
from kikoe.i18n import _
from kikoe.ui.labels import capability_summary, transcription_mode, unsupported_option_subtitle
from kikoe.ui.templates import ui_file

_GRANULARITIES: tuple[tuple[TimestampGranularity, ...], ...] = (
    (TimestampGranularity.SEGMENT,),
    (TimestampGranularity.WORD,),
    (TimestampGranularity.SEGMENT, TimestampGranularity.WORD),
)
"""Selections offered by the Timestamp Detail row, in its order."""


@Gtk.Template(filename=ui_file("transcription_dialog"))
class TranscriptionDialog(Adw.Dialog):
    """Changes apply at once, like preferences, so there is no Save button."""

    __gtype_name__ = "KikoeTranscriptionDialog"

    models_box: Gtk.Box = Gtk.Template.Child()
    options_group: Adw.PreferencesGroup = Gtk.Template.Child()
    timestamps_row: Adw.SwitchRow = Gtk.Template.Child()
    granularity_row: Adw.ComboRow = Gtk.Template.Child()
    speakers_row: Adw.SwitchRow = Gtk.Template.Child()

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
        self._updating = False
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
            subtitle=capability_summary(model_support(choice.provider.api, choice.model).recording),
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
        self.options_group.set_visible(choice is not None)
        if choice is None:
            return
        capabilities = model_support(choice.provider.api, choice.model).recording
        allowed = self._wanted.allowed_by(capabilities)
        self.options_group.set_description(transcription_mode(capabilities))
        self._updating = True
        try:
            for row, supported, active in (
                (self.timestamps_row, capabilities.timestamps, allowed.timestamps),
                (self.speakers_row, capabilities.diarization, allowed.diarization),
            ):
                row.set_sensitive(supported)
                row.set_active(active)
                row.set_subtitle("" if supported else unsupported_option_subtitle())
            self.granularity_row.set_visible(
                choice.provider.api is ApiKind.OPENROUTER and allowed.timestamps
            )
            wanted = normalize_granularities(self._wanted.timestamp_granularities)
            self.granularity_row.set_selected(
                _GRANULARITIES.index(wanted) if wanted in _GRANULARITIES else 0
            )
        finally:
            self._updating = False

    @Gtk.Template.Callback()
    def _on_option_row_notify_active(self, _row: Adw.SwitchRow, _spec: GObject.ParamSpec) -> None:
        if self._updating or self._choice is None:
            return
        capabilities = model_support(self._choice.provider.api, self._choice.model).recording
        # A row the model cannot honor keeps the earlier wish for the next model that can.
        self._wanted = replace(
            self._wanted,
            timestamps=self.timestamps_row.get_active()
            if capabilities.timestamps
            else self._wanted.timestamps,
            diarization=self.speakers_row.get_active()
            if capabilities.diarization
            else self._wanted.diarization,
        )
        self._show_options()
        self._on_change(self._choice, self._wanted)

    @Gtk.Template.Callback()
    def _on_granularity_row_notify_selected(
        self, row: Adw.ComboRow, _spec: GObject.ParamSpec
    ) -> None:
        index = row.get_selected()
        if self._updating or self._choice is None or index >= len(_GRANULARITIES):
            return
        self._wanted = replace(self._wanted, timestamp_granularities=_GRANULARITIES[index])
        self._on_change(self._choice, self._wanted)

    @Gtk.Template.Callback()
    def _on_manage_row_activated(self, _row: Adw.ButtonRow) -> None:
        # Close first so Preferences is not stacked on a dialog whose models it may change.
        window = self.get_root()
        self.close()
        if isinstance(window, Gtk.Widget):
            window.activate_action("app.preferences", None)
