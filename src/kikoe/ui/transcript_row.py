"""One row of the note view: either a recording's header or one transcript segment."""

from datetime import datetime

from gi.repository import Gio, GLib, Gtk

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
    speaker_label: Gtk.Label = Gtk.Template.Child()
    timestamp_label: Gtk.Label = Gtk.Template.Child()
    text_label: Gtk.Label = Gtk.Template.Child()

    def bind(self, item: TranscriptItem, show_timestamps: bool, show_speakers: bool) -> None:
        """Fill the row; the meta column is shown or hidden for the whole note at once."""
        segment = item.segment
        self.header_box.set_visible(segment is None)
        self.segment_box.set_visible(segment is not None)
        if segment is None:
            self.title_label.set_label(_("Recording {number}").format(number=item.number))
            self.meta_label.set_label(recording_meta(item.recording, datetime.now().astimezone()))
            self.recording_menu_button.set_menu_model(_recording_menu(item.recording.recording_id))
            state = item.recording.transcription_state
            self.pending_box.set_visible(state is not TranscriptionState.READY)
            self.pending_label.set_label(
                _("Transcription failed. Your audio has been kept.")
                if state is TranscriptionState.FAILED
                else _("Audio saved. Transcription is pending.")
            )
            self.retry_button.set_action_name(None)
            self.retry_button.set_action_target_value(
                GLib.Variant("s", item.recording.recording_id)
            )
            self.retry_button.set_action_name("win.retry-recording")
            self.recording_menu_button.set_visible(True)
            return
        self.recording_menu_button.set_visible(False)
        self.meta_box.set_visible(show_speakers or show_timestamps)
        self.meta_box.set_size_request(
            _META_WIDTH_WITH_SPEAKERS if show_speakers else _META_WIDTH_TIMES_ONLY, -1
        )
        self.speaker_label.set_visible(show_speakers and item.speaker_name is not None)
        self.speaker_label.set_label(item.speaker_name or "")
        for css_class in _SPEAKER_CLASSES:
            self.speaker_label.remove_css_class(css_class)
        if item.color_slot is not None:
            self.speaker_label.add_css_class(_SPEAKER_CLASSES[item.color_slot])
        start = segment.start
        self.timestamp_label.set_visible(show_timestamps and start is not None)
        self.timestamp_label.set_label(format_timestamp(start) if start is not None else "")
        self.text_label.set_label(segment.text)


def _recording_menu(recording_id: str) -> Gio.Menu:
    """Per-row menu; built in code because recycled rows cannot carry the id in Blueprint."""
    entry = Gio.MenuItem()
    entry.set_label(_("Export Original Audio…"))
    entry.set_action_and_target_value("win.export-audio", GLib.Variant("s", recording_id))
    menu = Gio.Menu()
    menu.append_item(entry)
    return menu
