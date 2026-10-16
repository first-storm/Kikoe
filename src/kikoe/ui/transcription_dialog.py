"""Choose the model and options the next recording or import is transcribed with."""

from collections.abc import Callable, Sequence

from gi.repository import Adw, Gtk

from kikoe.core.choice import ModelChoice, RequestedFeatures
from kikoe.i18n import _
from kikoe.ui.labels import model_choice_subtitle, transcription_mode
from kikoe.ui.model_chooser_page import ModelChooserPage
from kikoe.ui.options_group import OptionsGroup
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("transcription_dialog"))
class TranscriptionDialog(Adw.Dialog):
    """Changes apply at once, like preferences, so there is no Save button.

    The model list lives on a pushed page: with many models, a single page buried
    the options below the list.
    """

    __gtype_name__ = "KikoeTranscriptionDialog"

    navigation_view: Adw.NavigationView = Gtk.Template.Child()
    model_group: Adw.PreferencesGroup = Gtk.Template.Child()
    model_row: Adw.ActionRow = Gtk.Template.Child()
    options_slot: Gtk.Box = Gtk.Template.Child()

    def __init__(
        self,
        choices: Sequence[ModelChoice],
        current: ModelChoice | None,
        wanted: RequestedFeatures,
        on_change: Callable[[ModelChoice, RequestedFeatures], None],
        language: str | None = None,
    ) -> None:
        super().__init__()
        self._language = language
        self._choice = current
        self._wanted = wanted
        self._on_change = on_change
        self._options = OptionsGroup()
        self._options.connect("changed", self._on_options_group_changed)
        self.options_slot.append(self._options)
        self._chooser = ModelChooserPage(choices, None, self._on_model_chosen)
        self.model_row.set_visible(bool(choices))
        if not choices:
            self.model_group.set_description(_("Add a provider and a model to start transcribing."))
        self._show_choice()
        if choices:
            self.set_focus(self.model_row)

    def _show_choice(self) -> None:
        choice = self._choice
        self.options_slot.set_visible(choice is not None)
        if choice is None:
            return
        capabilities = choice.model.capabilities
        self.model_row.set_title(choice.model.display_name)
        self.model_row.set_subtitle(model_choice_subtitle(choice))
        self._options.set_description(transcription_mode(capabilities))
        # Only uploads send the language; live sessions have their own rules.
        uploads = capabilities.realtime is None
        self._options.set_options(
            capabilities.recording,
            self._wanted,
            choice.provider.api if uploads else None,
            self._language,
        )

    def _on_model_chosen(self, choice: ModelChoice) -> None:
        # Both the radio and its row report one click; only the first changes anything.
        if self._choice is None or choice.key != self._choice.key:
            self._choice = choice
            self._show_choice()
            self._on_change(choice, self._wanted)
        if self.navigation_view.get_visible_page() is self._chooser:
            self.navigation_view.pop()

    def _on_options_group_changed(self, group: OptionsGroup) -> None:
        self._wanted = group.wanted
        if self._choice is not None:
            self._on_change(self._choice, self._wanted)

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
    def _on_manage_row_activated(self, _row: Adw.ButtonRow) -> None:
        # Close first so Preferences is not stacked on a dialog whose models it may change.
        window = self.get_root()
        self.close()
        if isinstance(window, Gtk.Widget):
            window.activate_action("app.manage-providers", None)
