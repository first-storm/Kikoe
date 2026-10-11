"""One row of the note view: either a recording's header or one transcript segment."""

from datetime import datetime, timedelta

from gi.repository import Gtk

from kikoe.core.formatting import format_timestamp
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.labels import recording_meta
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("transcript_row"))
class TranscriptRow(Gtk.Box):
    __gtype_name__ = "KikoeTranscriptRow"

    header_box: Gtk.Box = Gtk.Template.Child()
    title_label: Gtk.Label = Gtk.Template.Child()
    meta_label: Gtk.Label = Gtk.Template.Child()
    segment_box: Gtk.Box = Gtk.Template.Child()
    speaker_label: Gtk.Label = Gtk.Template.Child()
    timestamp_label: Gtk.Label = Gtk.Template.Child()
    text_label: Gtk.Label = Gtk.Template.Child()

    def bind(self, item: TranscriptItem, show_timestamps: bool, show_speakers: bool) -> None:
        """Fill the row; the speaker column is shown or hidden for the whole note at once."""
        segment = item.segment
        self.header_box.set_visible(segment is None)
        self.segment_box.set_visible(segment is not None)
        if segment is None:
            self.title_label.set_label(_("Recording {number}").format(number=item.number))
            self.meta_label.set_label(recording_meta(item.recording, datetime.now().astimezone()))
            return
        self.speaker_label.set_visible(show_speakers)
        self.speaker_label.set_label(item.speaker_name or "")
        start = segment.start
        self.timestamp_label.set_visible(show_timestamps and start is not None)
        # Keep the text column aligned when some times need an hour field (1:02:03).
        long = item.recording.duration >= timedelta(hours=1)
        self.timestamp_label.set_width_chars(7 if long else 5)
        self.timestamp_label.set_label(format_timestamp(start) if start is not None else "")
        self.text_label.set_label(segment.text)
