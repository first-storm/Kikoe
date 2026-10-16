"""Bar below the note: model button, Import Audio, Record and progress."""

import math
from dataclasses import replace

from gi.repository import Adw, Gdk, GObject, Gtk

from kikoe.core.activity import Stage
from kikoe.core.choice import ModelChoice, RequestedFeatures, available_models, resolve_choice
from kikoe.core.formatting import format_timestamp
from kikoe.core.models import ModeCapabilities
from kikoe.core.recording import CaptureProgress, CaptureState
from kikoe.core.transcription import LiveState
from kikoe.i18n import _
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.labels import live_status_label, stage_label, transcription_hint
from kikoe.ui.level_meter import LevelMeter
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file
from kikoe.ui.transcription_dialog import TranscriptionDialog

_PAGES = {Stage.IDLE: "idle", Stage.RECORDING: "recording"}
"""Every other stage is shown as progress."""


@Gtk.Template(filename=ui_file("bottom_bar"))
class BottomBar(Adw.Bin):
    __gtype_name__ = "KikoeBottomBar"

    model_button: Gtk.Button = Gtk.Template.Child()
    model_label: Gtk.Label = Gtk.Template.Child()
    provider_label: Gtk.Label = Gtk.Template.Child()
    state_stack: Gtk.Stack = Gtk.Template.Child()
    record_content: Adw.ButtonContent = Gtk.Template.Child()
    elapsed_label: Gtk.Label = Gtk.Template.Child()
    recording_dot: Gtk.Image = Gtk.Template.Child()
    recording_summary: Gtk.Label = Gtk.Template.Child()
    pause_button: Gtk.Button = Gtk.Template.Child()
    meter_slot: Adw.Bin = Gtk.Template.Child()
    progress_label: Gtk.Label = Gtk.Template.Child()
    import_button: Gtk.Button = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    connection_spinner: Adw.Spinner = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self._stage = Stage.IDLE
        self._recording_state = CaptureState.STOPPED
        self._recording_summary = ""
        self.recording_dot.add_tick_callback(self._pulse_dot)
        self._meter = LevelMeter()
        self.meter_slot.set_child(self._meter)
        self._providers: ProviderRepository | None = None
        self._preferences: PreferencesRepository | None = None
        self._choice: ModelChoice | None = None
        self._wanted = RequestedFeatures()
        """What the user asked for, kept while a model that lacks a feature is chosen."""
        self._defaults_pending = False

    @GObject.Signal
    def choice_changed(self) -> None:
        """The chosen model changed, or appeared or disappeared."""

    @property
    def choice(self) -> ModelChoice | None:
        return self._choice

    @property
    def requested_features(self) -> RequestedFeatures:
        """The options' state, limited to what the chosen model supports."""
        return self._allowed(self._choice.model.capabilities.recording if self._choice else None)

    @property
    def file_features(self) -> RequestedFeatures:
        """Import and retry use file capabilities even when recording would stream."""
        return self._allowed(self._choice.model.capabilities.file if self._choice else None)

    def _allowed(self, mode: ModeCapabilities | None) -> RequestedFeatures:
        return (
            self._wanted.allowed_by(mode) if mode is not None else RequestedFeatures(False, False)
        )

    def bind(self, providers: ProviderRepository, preferences: PreferencesRepository) -> None:
        self._providers = providers
        self._preferences = preferences
        providers.connect_changed(self._refresh)
        preferences.connect_changed(self._on_preferences_changed)
        self._refresh()

    def _on_preferences_changed(self) -> None:
        if self._stage is not Stage.IDLE:
            self._defaults_pending = True
            return
        self._choice = None
        self._refresh()

    def set_stage(self, stage: Stage, message: str = "") -> None:
        """Show the page for ``stage``; the window calls this and nothing else drives the bar."""
        self._stage = stage
        text = message or stage_label(stage)
        if stage is Stage.RECORDING:
            self._recording_summary = text
            self.connection_spinner.set_visible(False)
            self.recording_summary.set_label(text)
        else:
            self.progress_label.set_label(text)
        self.cancel_button.set_visible(stage.shows_cancel)
        self.state_stack.set_visible_child_name(_PAGES.get(stage, "progress"))
        if stage is Stage.IDLE and self._defaults_pending:
            self._defaults_pending = False
            self._on_preferences_changed()

    def set_note(self, has_recordings: bool) -> None:
        self.record_content.set_label(_("_Append Recording") if has_recordings else _("_Record"))

    def set_live_state(self, state: LiveState) -> None:
        """Keep capture controls available while the recognizer prepares or reconnects."""
        waiting = state in {LiveState.CONNECTING, LiveState.RECONNECTING, LiveState.CATCHING_UP}
        self.connection_spinner.set_visible(waiting)
        message = self._recording_summary
        if waiting:
            message = (
                _("Preparing transcription…")
                if state is LiveState.CONNECTING
                else live_status_label(state)
            )
        self.recording_summary.set_label(message)
        self.connection_spinner.set_tooltip_text(message)

    def update_recording(self, progress: CaptureProgress) -> None:
        """Capture measurements arrive on the application's asyncio/GTK thread."""
        self._recording_state = progress.state
        paused = progress.state is CaptureState.PAUSED
        self.elapsed_label.set_label(format_timestamp(progress.elapsed))
        self._meter.set_level(progress.level)
        if progress.state is not CaptureState.RECORDING:
            self._meter.clear()
        self.recording_dot.set_opacity(0.35 if paused else 1)
        self.pause_button.set_icon_name(
            "media-playback-start-symbolic" if paused else "media-playback-pause-symbolic"
        )
        self.pause_button.set_action_name(
            "win.resume-recording" if paused else "win.pause-recording"
        )
        label = _("Resume Recording") if paused else _("Pause Recording")
        self.pause_button.set_tooltip_text(label)
        self.pause_button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def _pulse_dot(self, _widget: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
        if self._stage is Stage.RECORDING and self._recording_state is CaptureState.RECORDING:
            settings = self.get_settings()
            animations = bool(settings.get_property("gtk-enable-animations"))
            opacity = 0.65 + 0.35 * math.cos(clock.get_frame_time() / 1_400_000 * math.tau)
            self.recording_dot.set_opacity(opacity if animations else 1)
        return True

    def _refresh(self) -> None:
        """Keep the current model if it still exists, else the recent, default or first one."""
        if self._providers is None or self._preferences is None:
            return
        current = self._preferences.current
        self._choice = resolve_choice(
            self._providers.providers,
            self._choice.key if self._choice else None,
            current.recent_model,
            current.default_model,
        )
        self._show_choice()

    def _show_choice(self) -> None:
        choice = self._choice
        if choice is None:
            self.model_label.set_label(_("No Model"))
            self.provider_label.set_label("")
            self.import_button.set_tooltip_text(_("Add a model in Preferences first"))
            self.model_button.set_tooltip_text(_("Transcription Model…"))
        else:
            self.model_label.set_label(choice.model.display_name)
            self.provider_label.set_label(choice.provider.name)
            self.model_button.set_tooltip_text(
                _("{model} · {provider}\n{hint}").format(
                    model=choice.model.display_name,
                    provider=choice.provider.name,
                    hint=transcription_hint(choice.model.capabilities),
                )
            )
            self.import_button.set_tooltip_text(
                _("Import Audio…")
                if choice.model.capabilities.file is not None
                else _("Choose a file transcription model to import audio.")
            )
        self.emit("choice-changed")

    def present_transcription_dialog(self) -> None:
        """Let the user pick the model and options for the next transcription."""
        providers = self._providers.providers if self._providers else ()
        language = self._preferences.current.language if self._preferences else None
        TranscriptionDialog(
            available_models(providers),
            self._choice,
            self._wanted,
            self._on_dialog_changed,
            language,
        ).present(self)

    def _on_dialog_changed(self, choice: ModelChoice, wanted: RequestedFeatures) -> None:
        self._wanted = wanted
        if self._choice is not None and choice.key == self._choice.key:
            return
        self._choice = choice
        if self._preferences is not None:
            key = choice.key
            run_task(self._preferences.update(lambda p: replace(p, recent_model=key)), self)
        self._show_choice()
