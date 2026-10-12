"""Bar below the note: model picker with its options, Import Audio, Record and progress."""

import math
from dataclasses import replace

from gi.repository import Adw, Gdk, Gio, GObject, Gtk, Pango

from kikoe.core.choice import (
    ModelChoice,
    RequestedFeatures,
    available_models,
    resolve_choice,
)
from kikoe.core.formatting import format_timestamp
from kikoe.core.models import ModelSpec
from kikoe.core.preferences import preferred_model
from kikoe.core.recording import CaptureProgress, CaptureState
from kikoe.core.transcription import model_support
from kikoe.core.validation import Capability
from kikoe.i18n import _
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.items import ModelChoiceItem
from kikoe.ui.labels import capability_summary, transcription_hint, unsupported_feature_tooltip
from kikoe.ui.level_meter import LevelMeter
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("bottom_bar"))
class BottomBar(Adw.Bin):
    __gtype_name__ = "KikoeBottomBar"

    model_button: Gtk.MenuButton = Gtk.Template.Child()
    model_label: Gtk.Label = Gtk.Template.Child()
    provider_label: Gtk.Label = Gtk.Template.Child()
    options_box: Gtk.Box = Gtk.Template.Child()
    timestamps_check: Gtk.CheckButton = Gtk.Template.Child()
    speakers_check: Gtk.CheckButton = Gtk.Template.Child()
    hint_label: Gtk.Label = Gtk.Template.Child()
    model_popover: Gtk.Popover = Gtk.Template.Child()
    model_list: Gtk.ListBox = Gtk.Template.Child()
    state_stack: Gtk.Stack = Gtk.Template.Child()
    record_button: Gtk.Button = Gtk.Template.Child()
    record_content: Adw.ButtonContent = Gtk.Template.Child()
    elapsed_label: Gtk.Label = Gtk.Template.Child()
    recording_dot: Gtk.Image = Gtk.Template.Child()
    recording_summary: Gtk.Label = Gtk.Template.Child()
    pause_button: Gtk.Button = Gtk.Template.Child()
    meter_slot: Adw.Bin = Gtk.Template.Child()
    progress_label: Gtk.Label = Gtk.Template.Child()
    import_button: Gtk.Button = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self._recording_state = CaptureState.STOPPED
        self.recording_dot.add_tick_callback(self._pulse_dot)
        self._meter = LevelMeter()
        self.meter_slot.set_child(self._meter)
        self._repository: ProviderRepository | None = None
        self._choice: ModelChoice | None = None
        self._wanted = RequestedFeatures()
        """What the user asked for, kept while a model that lacks a feature is chosen."""
        self._updating = False
        self._defaults_pending = False
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
        """The options' state, limited to what the chosen model supports."""
        if self._choice is None:
            return RequestedFeatures(timestamps=False, diarization=False)
        return self._wanted.allowed_by(
            model_support(self._choice.provider.api, self._choice.model).recording
        )

    @property
    def file_features(self) -> RequestedFeatures:
        """Import and retry use file capabilities even when recording would stream."""
        if self._choice is None:
            return RequestedFeatures(False, False)
        return self._wanted.allowed_by(
            model_support(self._choice.provider.api, self._choice.model).batch
        )

    def bind_providers(self, repository: ProviderRepository) -> None:
        self._repository = repository
        repository.connect_changed(self._refresh)
        repository.connect_preferences_changed(self._on_preferences_changed)
        self._refresh()

    def _on_preferences_changed(self) -> None:
        if self.state_stack.get_visible_child_name() != "idle":
            self._defaults_pending = True
            return
        if self._repository is not None:
            self._choice = preferred_model(self._repository.providers, self._repository.preferences)
            self._refresh()

    def set_progress(self, message: str | None) -> None:
        """Show ``message`` with a spinner and a Cancel button, or the controls when None."""
        self.progress_label.set_label(message or "")
        self.state_stack.set_visible_child_name("progress" if message is not None else "idle")
        if message is None and self._defaults_pending:
            self._defaults_pending = False
            self._on_preferences_changed()

    def set_note(self, has_recordings: bool) -> None:
        self.record_content.set_label(_("_Append Recording") if has_recordings else _("_Record"))

    def show_recording(self, summary: str) -> None:
        self.recording_summary.set_label(summary)
        self.state_stack.set_visible_child_name("recording")

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
        if self.state_stack.get_visible_child_name() != "recording":
            return True
        if self._recording_state is CaptureState.RECORDING:
            settings = self.get_settings()
            animations = bool(settings.get_property("gtk-enable-animations"))
            opacity = 0.65 + 0.35 * math.cos(clock.get_frame_time() / 1_400_000 * math.tau)
            self.recording_dot.set_opacity(opacity if animations else 1)
        return True

    def _refresh(self) -> None:
        providers = self._repository.providers if self._repository else ()
        wanted = self._choice.key if self._choice else None
        choices = available_models(providers)
        self._choice = (
            resolve_choice(providers, wanted)
            if any(c.key == wanted for c in choices)
            else preferred_model(providers, self._repository.preferences)
            if self._repository
            else None
        )
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
            self.model_button.set_tooltip_text(_("Transcription Model"))
        else:
            self.model_label.set_label(choice.model.display_name)
            self.provider_label.set_label(choice.provider.name)
            self.model_button.set_tooltip_text(
                _("{model} · {provider}\n{hint}").format(
                    model=choice.model.display_name,
                    provider=choice.provider.name,
                    hint=transcription_hint(
                        model_support(choice.provider.api, choice.model).recording
                    ),
                )
            )
            self.import_button.set_tooltip_text(
                _("Import Audio…")
                if model_support(choice.provider.api, choice.model).file
                else _("Choose a file transcription model to import audio.")
            )
        self._show_options(
            replace(
                choice.model,
                capabilities=model_support(choice.provider.api, choice.model).recording,
            )
            if choice
            else None
        )
        self.emit("choice-changed")

    def _show_options(self, model: ModelSpec | None) -> None:
        self.options_box.set_visible(model is not None)
        if model is None:
            self.hint_label.set_label("")
            self.hint_label.set_tooltip_text(None)
            return
        allowed = self._wanted.allowed_by(model.capabilities)
        checks = (
            (
                self.timestamps_check,
                Capability.TIMESTAMPS,
                model.capabilities.timestamps,
                allowed.timestamps,
            ),
            (
                self.speakers_check,
                Capability.DIARIZATION,
                model.capabilities.diarization,
                allowed.diarization,
            ),
        )
        self._updating = True
        try:
            for check, capability, supported, active in checks:
                check.set_sensitive(supported)
                check.set_active(active)
                check.set_tooltip_text(
                    None
                    if supported
                    else unsupported_feature_tooltip(capability, model.display_name)
                )
        finally:
            self._updating = False
        hint = transcription_hint(model.capabilities)
        self.hint_label.set_label(hint)
        self.hint_label.set_tooltip_text(hint)

    @Gtk.Template.Callback()
    def _on_feature_check_toggled(self, _check: Gtk.CheckButton) -> None:
        if self._updating or self._choice is None:
            return
        caps = model_support(self._choice.provider.api, self._choice.model).recording
        self._wanted = RequestedFeatures(
            timestamps=self.timestamps_check.get_active()
            if caps.timestamps
            else self._wanted.timestamps,
            diarization=self.speakers_check.get_active()
            if caps.diarization
            else self._wanted.diarization,
        )
        self._show_options(replace(self._choice.model, capabilities=caps))

    def _create_model_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, ModelChoiceItem)
        choice = item.choice
        title = Gtk.Label(
            label=choice.model.display_name, xalign=0, ellipsize=Pango.EllipsizeMode.END
        )
        subtitle = Gtk.Label(
            label=capability_summary(model_support(choice.provider.api, choice.model).recording),
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
        if self._repository is not None:
            key = item.choice.key
            run_task(
                self._repository.update_preferences(lambda p: replace(p, recent_model=key)), self
            )
        # Rebuild the rows so the check mark moves.
        self._refresh()
