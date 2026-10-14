"""Timestamps, timestamp detail and speakers switches shared by every model picker."""

from dataclasses import replace

from gi.repository import Adw, GObject, Gtk

from kikoe.core.choice import RequestedFeatures, language_dropped, normalize_granularities
from kikoe.core.models import ApiKind, ModeCapabilities, TimestampGranularity
from kikoe.ui.labels import unsupported_option_subtitle
from kikoe.ui.templates import ui_file

_GRANULARITIES: tuple[tuple[TimestampGranularity, ...], ...] = (
    (TimestampGranularity.SEGMENT,),
    (TimestampGranularity.WORD,),
    (TimestampGranularity.SEGMENT, TimestampGranularity.WORD),
)
"""Selections offered by the Timestamp Detail row, in its order."""


@Gtk.Template(filename=ui_file("options_group"))
class OptionsGroup(Adw.PreferencesGroup):
    """Rows the model cannot honor are disabled but keep the earlier wish for the next model."""

    __gtype_name__ = "KikoeOptionsGroup"

    timestamps_row: Adw.SwitchRow = Gtk.Template.Child()
    granularity_row: Adw.ComboRow = Gtk.Template.Child()
    speakers_row: Adw.SwitchRow = Gtk.Template.Child()
    language_notice: Gtk.Label = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self._capabilities = ModeCapabilities()
        self._wanted = RequestedFeatures()
        self._api: ApiKind | None = None
        self._language: str | None = None
        self._updating = False

    @GObject.Signal
    def changed(self) -> None:
        """The user changed an option; read :attr:`wanted`."""

    @property
    def wanted(self) -> RequestedFeatures:
        """Everything the user asked for, including options the current model lacks."""
        return self._wanted

    @property
    def allowed(self) -> RequestedFeatures:
        """What a request with the current model will actually ask for."""
        return self._wanted.allowed_by(self._capabilities)

    def set_options(
        self,
        capabilities: ModeCapabilities,
        wanted: RequestedFeatures,
        api: ApiKind | None = None,
        language: str | None = None,
    ) -> None:
        """``api`` and ``language`` describe an upload, so the language notice can be shown."""
        self._capabilities = capabilities
        self._wanted = wanted
        self._api = api
        self._language = language
        self._refresh()

    def _refresh(self) -> None:
        capabilities = self._capabilities
        allowed = self.allowed
        self._updating = True
        try:
            for row, supported, active in (
                (self.timestamps_row, capabilities.timestamps, allowed.timestamps),
                (self.speakers_row, capabilities.diarization, allowed.diarization),
            ):
                row.set_sensitive(supported)
                row.set_active(active)
                row.set_subtitle("" if supported else unsupported_option_subtitle())
            self.language_notice.set_visible(
                self._api is not None and language_dropped(self._api, allowed, self._language)
            )
            self.granularity_row.set_visible(capabilities.word_timestamps and allowed.timestamps)
            wanted = normalize_granularities(self._wanted.timestamp_granularities)
            self.granularity_row.set_selected(
                _GRANULARITIES.index(wanted) if wanted in _GRANULARITIES else 0
            )
        finally:
            self._updating = False

    @Gtk.Template.Callback()
    def _on_option_row_notify_active(self, _row: Adw.SwitchRow, _spec: GObject.ParamSpec) -> None:
        if self._updating:
            return
        capabilities = self._capabilities
        self._wanted = replace(
            self._wanted,
            timestamps=self.timestamps_row.get_active()
            if capabilities.timestamps
            else self._wanted.timestamps,
            diarization=self.speakers_row.get_active()
            if capabilities.diarization
            else self._wanted.diarization,
        )
        self._refresh()
        self.emit("changed")

    @Gtk.Template.Callback()
    def _on_granularity_row_notify_selected(
        self, row: Adw.ComboRow, _spec: GObject.ParamSpec
    ) -> None:
        index = row.get_selected()
        if self._updating or index >= len(_GRANULARITIES):
            return
        self._wanted = replace(self._wanted, timestamp_granularities=_GRANULARITIES[index])
        self.emit("changed")
