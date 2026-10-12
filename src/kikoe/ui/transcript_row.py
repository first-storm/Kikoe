"""One row of the note view: either a recording's header or one transcript segment."""

from datetime import datetime

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from kikoe.core.formatting import SPEAKER_COLOR_COUNT, format_timestamp
from kikoe.core.models import TranscriptionState
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.labels import recording_meta
from kikoe.ui.templates import ui_file

_SPEAKER_CLASSES = tuple(f"speaker-{slot}" for slot in range(SPEAKER_COLOR_COUNT))
"""Defined in style.css with libadwaita palette colors."""

_META_WIDTH_WITH_SPEAKERS = 96
_META_WIDTH_TIMES_ONLY = 56


@Gtk.Template(filename=ui_file("transcript_row"))
class TranscriptRow(Gtk.Box):
    __gtype_name__ = "KikoeTranscriptRow"

    pending_box: Gtk.Box = Gtk.Template.Child()
    pending_label: Gtk.Label = Gtk.Template.Child()
    retry_button: Gtk.Button = Gtk.Template.Child()
    header_box: Gtk.Box = Gtk.Template.Child()
    title_label: Gtk.Label = Gtk.Template.Child()
    meta_label: Gtk.Label = Gtk.Template.Child()
    recording_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    segment_box: Gtk.Box = Gtk.Template.Child()
    meta_box: Gtk.Box = Gtk.Template.Child()
    play_button: Gtk.Button = Gtk.Template.Child()
    speaker_button: Gtk.Button = Gtk.Template.Child()
    speaker_label: Gtk.Label = Gtk.Template.Child()
    timestamp_label: Gtk.Label = Gtk.Template.Child()
    text_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self._preview_text: str | None = None
        self._caret_visible: bool | None = None
        self._color_slot: int | None = None
        self._dark: bool | None = None
        self.add_tick_callback(self._pulse_caret)

    def _pulse_caret(self, _widget: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
        self._update_speaker_color()
        if self._preview_text is None:
            return True
        animations = bool(self.get_settings().get_property("gtk-enable-animations"))
        visible = not animations or clock.get_frame_time() % 1_000_000 < 500_000
        if visible != self._caret_visible:
            self._caret_visible = visible
            accent = Adw.StyleManager.get_default().get_accent_color_rgba()
            color = "#" + "".join(
                f"{round(channel * 255):02x}" for channel in (accent.red, accent.green, accent.blue)
            )
            text = GLib.markup_escape_text(self._preview_text)
            self.text_label.set_markup(
                f'{text}<span foreground="{color}" alpha="{65535 if visible else 1}">▏</span>'
            )
        return True

    def bind(self, item: TranscriptItem, show_timestamps: bool, show_speakers: bool) -> None:
        """Fill the row; the meta column is shown or hidden for the whole note at once."""
        segment = item.segment
        self.header_box.set_visible(segment is None)
        self.segment_box.set_visible(segment is not None)
        if segment is None:
            self._preview_text = None
            self.title_label.set_label(_("Recording {number}").format(number=item.number))
            self.meta_label.set_label(
                item.live_summary or recording_meta(item.recording, datetime.now().astimezone())
            )
            self.recording_menu_button.set_menu_model(_recording_menu(item.recording.recording_id))
            self.play_button.set_visible(item.live_summary is None)
            self.play_button.set_action_name(None)
            self.play_button.set_action_target_value(GLib.Variant("s", item.recording.recording_id))
            self.play_button.set_action_name("win.play-recording")
            self.play_button.set_sensitive(item.recording.audio_path is not None)
            state = item.recording.transcription_state
            self.pending_box.set_visible(
                state is not TranscriptionState.READY and item.live_summary is None
            )
            self.pending_label.set_label(
                (
                    _("Transcription incomplete. Your text and audio have been kept.")
                    if item.recording.segments
                    else _("Transcription failed. Your audio has been kept.")
                )
                if state is TranscriptionState.FAILED
                else _("Audio saved. Transcription is pending.")
            )
            self.retry_button.set_action_name(None)
            self.retry_button.set_action_target_value(
                GLib.Variant("s", item.recording.recording_id)
            )
            self.retry_button.set_action_name("win.retry-recording")
            self.recording_menu_button.set_visible(item.live_summary is None)
            return
        self.recording_menu_button.set_visible(False)
        self.meta_box.set_visible(show_speakers or show_timestamps)
        self.meta_box.set_size_request(
            _META_WIDTH_WITH_SPEAKERS if show_speakers else _META_WIDTH_TIMES_ONLY, -1
        )
        self.speaker_button.set_visible(show_speakers and item.speaker_name is not None)
        self.speaker_button.set_action_name(None)
        self.speaker_button.set_action_target_value(
            GLib.Variant("(ss)", (item.recording.recording_id, segment.speaker_id or ""))
        )
        self.speaker_button.set_action_name("win.edit-speaker")
        self.speaker_button.set_sensitive(
            not item.preview and item.recording.recording_id != "live"
        )
        self.speaker_label.set_label(item.speaker_name or "")
        self._color_slot = item.color_slot
        self._dark = None
        self._update_speaker_color()
        start = segment.start
        self.timestamp_label.set_visible(show_timestamps and start is not None)
        self.timestamp_label.set_label(format_timestamp(start) if start is not None else "")
        self.text_label.set_text(segment.text)
        self._preview_text = segment.text if item.preview else None
        self._caret_visible = None
        if item.preview:
            self.text_label.add_css_class("dim-label")
        else:
            self.text_label.remove_css_class("dim-label")

    def set_playing(self, playing: bool) -> None:
        """The header's single button toggles playback rather than overlapping audio."""
        self.play_button.set_icon_name(
            "media-playback-stop-symbolic" if playing else "media-playback-start-symbolic"
        )
        label = _("Stop Playback") if playing else _("Play This Recording")
        self.play_button.set_tooltip_text(label)
        self.play_button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def _update_speaker_color(self) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        if dark == self._dark:
            return
        self._dark = dark
        for css_class in _SPEAKER_CLASSES:
            self.speaker_label.remove_css_class(css_class)
            self.speaker_label.remove_css_class(css_class + "-dark")
        if self._color_slot is not None:
            css = _SPEAKER_CLASSES[self._color_slot]
            self.speaker_label.add_css_class(css + "-dark" if dark else css)


def _recording_menu(recording_id: str) -> Gio.Menu:
    """Per-row menu; built in code because recycled rows cannot carry the id in Blueprint."""
    entry = Gio.MenuItem()
    entry.set_label(_("Export Original Audio…"))
    entry.set_action_and_target_value("win.export-audio", GLib.Variant("s", recording_id))
    menu = Gio.Menu()
    menu.append_item(entry)
    retranscribe = Gio.MenuItem()
    retranscribe.set_label(_("Re-transcribe…"))
    retranscribe.set_action_and_target_value(
        "win.retranscribe-recording", GLib.Variant("s", recording_id)
    )
    menu.prepend_item(retranscribe)
    match = Gio.MenuItem()
    match.set_label(_("Match Speakers…"))
    match.set_action_and_target_value("win.match-speakers", GLib.Variant("s", recording_id))
    menu.append_item(match)
    return menu
