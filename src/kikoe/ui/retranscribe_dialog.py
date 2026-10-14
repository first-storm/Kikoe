"""Choose file-model options before replacing one recording's transcript."""

from collections.abc import Callable

from gi.repository import Adw, GObject, Gtk

from kikoe.core.choice import ModelChoice, RequestedFeatures, available_models
from kikoe.core.formatting import format_duration
from kikoe.core.models import ModeCapabilities, Recording
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionOptions
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.labels import model_choice_label
from kikoe.ui.options_group import OptionsGroup
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("retranscribe_dialog"))
class RetranscribeDialog(Adw.Dialog):
    """Keep this request independent from the bottom bar's next-recording options."""

    __gtype_name__ = "KikoeRetranscribeDialog"
    intro_group: Adw.PreferencesGroup = Gtk.Template.Child()
    model_row: Adw.ComboRow = Gtk.Template.Child()
    options_slot: Gtk.Box = Gtk.Template.Child()
    transcribe_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        providers: ProviderRepository,
        preferences: PreferencesRepository,
        recording: Recording,
        number: int,
        current: ModelChoice | None,
        submit: Callable[[ModelChoice, TranscriptionOptions], None],
    ) -> None:
        super().__init__()
        self._updating = True
        self._providers, self._preferences, self._submit = providers, preferences, submit
        self._options = OptionsGroup()
        self.options_slot.append(self._options)
        self._choice = current if current and current.model.capabilities.file is not None else None
        wanted = RequestedFeatures(
            timestamps=any(s.start is not None for s in recording.segments),
            diarization=any(s.speaker_id is not None for s in recording.segments),
        )
        self.intro_group.set_description(
            _(
                "Re-transcribe “Recording {number}” ({duration}) from the kept original audio. "
                "Timestamps or speaker detection that were off during transcription "
                "can be added this way."
            ).format(number=number, duration=format_duration(recording.duration))
        )
        self._choices = [
            c
            for c in available_models(providers.providers)
            if c.model.capabilities.file is not None
        ]
        # Without a usable current model the first row is a prompt, so nothing is chosen silently.
        self._offset = 0 if self._choice is not None else 1
        names = [model_choice_label(c) for c in self._choices]
        if self._offset:
            names.insert(0, _("Choose a File Model…"))
        self.model_row.set_model(Gtk.StringList.new(names))
        if self._choice is not None:
            keys = [c.key for c in self._choices]
            self.model_row.set_selected(keys.index(self._choice.key))
        self._updating = False
        self._show_options(wanted)

    @Gtk.Template.Callback()
    def _on_model_row_notify_selected(self, _row: Adw.ComboRow, _spec: GObject.ParamSpec) -> None:
        if self._updating:
            return
        index = self.model_row.get_selected() - self._offset
        if not 0 <= index < len(self._choices):
            return
        self._choice = self._choices[index]
        self._show_options(self._options.wanted)

    def _show_options(self, wanted: RequestedFeatures) -> None:
        file = self._choice.model.capabilities.file if self._choice else None
        self.transcribe_button.set_sensitive(file is not None)
        self._options.set_sensitive(file is not None)
        self._options.set_options(
            file or ModeCapabilities(),
            wanted,
            self._choice.provider.api if self._choice else None,
            self._preferences.current.language,
        )

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    @Gtk.Template.Callback()
    def _on_transcribe_button_clicked(self, _button: Gtk.Button) -> None:
        if self._choice is None:
            return
        allowed = self._options.allowed
        self._submit(
            self._choice,
            TranscriptionOptions(
                timestamps=allowed.timestamps,
                diarization=allowed.diarization,
                language=self._preferences.current.language,
                timestamp_granularities=allowed.timestamp_granularities,
            ),
        )
        self.close()
