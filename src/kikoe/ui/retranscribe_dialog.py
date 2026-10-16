"""Choose file-model options before replacing one recording's transcript."""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from kikoe.core.choice import ModelChoice, RequestedFeatures, available_models
from kikoe.core.formatting import format_duration
from kikoe.core.models import ModeCapabilities, Recording
from kikoe.core.transcript_rows import transcript_columns
from kikoe.i18n import _
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.labels import model_choice_subtitle
from kikoe.ui.model_chooser_page import ModelChooserPage
from kikoe.ui.options_group import OptionsGroup
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("retranscribe_dialog"))
class RetranscribeDialog(Adw.Dialog):
    """Keep this request independent from the bottom bar's next-recording options."""

    __gtype_name__ = "KikoeRetranscribeDialog"
    navigation_view: Adw.NavigationView = Gtk.Template.Child()
    intro_group: Adw.PreferencesGroup = Gtk.Template.Child()
    model_row: Adw.ActionRow = Gtk.Template.Child()
    options_slot: Gtk.Box = Gtk.Template.Child()
    transcribe_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        providers: ProviderRepository,
        preferences: PreferencesRepository,
        recording: Recording,
        number: int,
        current: ModelChoice | None,
        submit: Callable[[ModelChoice, RequestedFeatures], None],
    ) -> None:
        super().__init__()
        self._preferences, self._submit = preferences, submit
        self._options = OptionsGroup()
        self.options_slot.append(self._options)
        # A live-only current model is not offered, so nothing is chosen silently.
        self._choice = current if current and current.model.capabilities.file is not None else None
        self.intro_group.set_description(
            _(
                "Re-transcribe “Recording {number}” ({duration}) from the kept original audio. "
                "Timestamps or speaker detection that were off during transcription "
                "can be added this way."
            ).format(number=number, duration=format_duration(recording.duration))
        )
        choices = [
            c
            for c in available_models(providers.providers)
            if c.model.capabilities.file is not None
        ]
        self._chooser = ModelChooserPage(choices, None, self._on_model_chosen, closable=False)
        self._show_choice(RequestedFeatures(*transcript_columns(recording.segments)))
        self.set_focus(self.model_row)

    def _show_choice(self, wanted: RequestedFeatures) -> None:
        choice = self._choice
        file = choice.model.capabilities.file if choice else None
        if choice is None:
            self.model_row.set_title(_("Choose a File Model"))
            self.model_row.set_subtitle(_("Required to re-transcribe"))
        else:
            self.model_row.set_title(choice.model.display_name)
            self.model_row.set_subtitle(model_choice_subtitle(choice))
        self.transcribe_button.set_sensitive(file is not None)
        self._options.set_sensitive(file is not None)
        self._options.set_options(
            file or ModeCapabilities(),
            wanted,
            choice.provider.api if choice else None,
            self._preferences.current.language,
        )

    def _on_model_chosen(self, choice: ModelChoice) -> None:
        # Both the radio and its row report one click; only the first changes anything.
        if self._choice is None or choice.key != self._choice.key:
            self._choice = choice
            self._show_choice(self._options.wanted)
        if self.navigation_view.get_visible_page() is self._chooser:
            self.navigation_view.pop()

    @Gtk.Template.Callback()
    def _on_model_row_activated(self, _row: Adw.ActionRow) -> None:
        self._chooser.set_current(self._choice.key if self._choice else None)
        self.navigation_view.push(self._chooser)

    @Gtk.Template.Callback()
    def _on_navigation_view_popped(
        self, _view: Adw.NavigationView, _page: Adw.NavigationPage
    ) -> None:
        """Return keyboard focus to where the chooser was opened from."""
        self.model_row.grab_focus()

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    @Gtk.Template.Callback()
    def _on_transcribe_button_clicked(self, _button: Gtk.Button) -> None:
        if self._choice is None:
            return
        self._submit(self._choice, self._options.allowed)
        self.close()
