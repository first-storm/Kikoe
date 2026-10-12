"""Choose file-model options before replacing one recording's transcript."""

from collections.abc import Callable

from gi.repository import Adw, GObject, Gtk

from kikoe.core.choice import ModelChoice, available_models
from kikoe.core.formatting import format_duration
from kikoe.core.models import Recording
from kikoe.core.transcription import model_support
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionOptions
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("retranscribe_dialog"))
class RetranscribeDialog(Adw.Dialog):
    """Keep this request independent from the bottom bar's next-recording options."""

    __gtype_name__ = "KikoeRetranscribeDialog"
    intro_group: Adw.PreferencesGroup = Gtk.Template.Child()
    model_row: Adw.ComboRow = Gtk.Template.Child()
    timestamps_row: Adw.SwitchRow = Gtk.Template.Child()
    speakers_row: Adw.SwitchRow = Gtk.Template.Child()
    transcribe_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        providers: ProviderRepository,
        recording: Recording,
        number: int,
        current: ModelChoice | None,
        submit: Callable[[ModelChoice, TranscriptionOptions], None],
    ) -> None:
        super().__init__()
        self._updating = True
        self._providers, self._submit = providers, submit
        self._choice = (
            current if current and model_support(current.provider.api, current.model).file else None
        )
        self._want_timestamps = any(s.start is not None for s in recording.segments)
        self._want_speakers = any(s.speaker_id is not None for s in recording.segments)
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
            if model_support(c.provider.api, c.model).file
        ]
        # Without a usable current model the first row is a prompt, so nothing is chosen silently.
        self._offset = 0 if self._choice is not None else 1
        names = [
            _("{provider} · {model}").format(provider=c.provider.name, model=c.model.display_name)
            for c in self._choices
        ]
        if self._offset:
            names.insert(0, _("Choose a File Model…"))
        self.model_row.set_model(Gtk.StringList.new(names))
        if self._choice is not None:
            keys = [c.key for c in self._choices]
            self.model_row.set_selected(keys.index(self._choice.key))
        self._updating = False
        self._update_options()

    @Gtk.Template.Callback()
    def _on_model_row_notify_selected(self, _row: Adw.ComboRow, _spec: GObject.ParamSpec) -> None:
        if self._updating:
            return
        index = self.model_row.get_selected() - self._offset
        if not 0 <= index < len(self._choices):
            return
        if self._choice is not None:
            if self.timestamps_row.get_sensitive():
                self._want_timestamps = self.timestamps_row.get_active()
            if self.speakers_row.get_sensitive():
                self._want_speakers = self.speakers_row.get_active()
        self._choice = self._choices[index]
        self._update_options()

    def _update_options(self) -> None:
        choice = self._choice
        self.transcribe_button.set_sensitive(choice is not None)
        capabilities = model_support(choice.provider.api, choice.model).batch if choice else None
        for row, supported, wanted in (
            (
                self.timestamps_row,
                capabilities.timestamps if capabilities else False,
                self._want_timestamps,
            ),
            (
                self.speakers_row,
                capabilities.diarization if capabilities else False,
                self._want_speakers,
            ),
        ):
            row.set_sensitive(supported)
            row.set_active(wanted and supported)
            row.set_tooltip_text(
                None if supported else _("This model does not support this option.")
            )

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    @Gtk.Template.Callback()
    def _on_transcribe_button_clicked(self, _button: Gtk.Button) -> None:
        if self._choice is not None:
            self._submit(
                self._choice,
                TranscriptionOptions(
                    timestamps=self.timestamps_row.get_active(),
                    diarization=self.speakers_row.get_active(),
                    language=self._providers.preferences.language,
                ),
            )
            self.close()
