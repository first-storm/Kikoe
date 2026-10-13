"""One row of the note view: either a recording's header or one transcript segment.

Segment text is edited in place. The row commits its text after a short pause in
typing, when the caret leaves it, on Enter and when it is recycled; the note view turns
commits into saves and Undo/Redo steps.
"""

from datetime import datetime, timedelta

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from kikoe.core.formatting import (
    SPEAKER_COLOR_COUNT,
    TimestampParseError,
    format_timestamp_input,
    format_timestamp_range,
    parse_timestamp,
)
from kikoe.core.models import Segment, TranscriptionState
from kikoe.core.validation import SegmentEditError, validate_segment_edit
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.labels import recording_meta
from kikoe.ui.segment_text import SegmentText
from kikoe.ui.tasks import show_notice
from kikoe.ui.templates import ui_file

_SPEAKER_CLASSES = tuple(f"speaker-{slot}" for slot in range(SPEAKER_COLOR_COUNT))
"""Defined in style.css with libadwaita palette colors."""

_META_WIDTH_WITH_SPEAKERS = 132
_META_WIDTH_TIMES_ONLY = 96
_COMMIT_DELAY_MS = 800
NO_TIME = -1
"""Signal value for a cleared timestamp; GObject signals cannot carry None for int64."""


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
    timestamp_button: Gtk.MenuButton = Gtk.Template.Child()
    timestamp_label: Gtk.Label = Gtk.Template.Child()
    timestamp_popover: Gtk.Popover = Gtk.Template.Child()
    start_row: Gtk.Entry = Gtk.Template.Child()
    end_row: Gtk.Entry = Gtk.Template.Child()
    times_error_label: Gtk.Label = Gtk.Template.Child()
    text_stack: Gtk.Stack = Gtk.Template.Child()
    text_slot: Adw.Bin = Gtk.Template.Child()
    text_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self) -> None:
        super().__init__()
        self.text_view = SegmentText()
        self.text_slot.set_child(self.text_view)
        self._preview_text: str | None = None
        self._caret_visible: bool | None = None
        self._pulse = 0
        self._color_slot: int | None = None
        self._dark: bool | None = None
        self._key: tuple[str, int] | None = None
        """(recording id, position) of the editable segment shown, if any."""
        self._segment: Segment | None = None
        self._committed = ""
        """The text last saved or loaded; typing beyond it is not committed yet."""
        self._loading = False
        self._loading_times = False
        self._preedit = False
        self._commit_source = 0
        buffer = self.text_view.get_buffer()
        # The note view keeps one history for the whole note instead.
        buffer.set_enable_undo(False)
        buffer.connect("changed", self._on_buffer_changed)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_text_key_pressed)
        self.text_view.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", self._on_text_focus_leave)
        self.text_view.add_controller(focus)
        self.text_view.connect("keynav-failed", self._on_text_keynav_failed)
        self.text_view.connect("preedit-changed", self._on_text_preedit_changed)

    __gsignals__ = {  # noqa: RUF012  # PyGObject reads this class attribute once
        # The text changed and should be saved.
        "text-committed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # The timestamps changed, in milliseconds; NO_TIME clears one.
        "times-committed": (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        # Move the caret to the previous (-1) or next (+1) segment.
        "navigate": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        # The caret left this segment, which ends the current Undo step.
        "focus-left": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def _pulse_caret(self, _widget: Gtk.Widget, clock: Gdk.FrameClock) -> bool:
        if self._preview_text is None:
            self._pulse = 0
            return False
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

    def bind(
        self,
        item: TranscriptItem,
        *,
        show_timestamps: bool,
        show_speakers: bool,
        compact: bool,
        editable: bool,
    ) -> None:
        """Fill the row; the meta column is shown or hidden for the whole note at once."""
        segment = item.segment
        self.header_box.set_visible(segment is None)
        self.segment_box.set_visible(segment is not None)
        if segment is None:
            self._bind_header(item)
            return
        self.recording_menu_button.set_visible(False)
        self._bind_meta(item, segment, show_timestamps, show_speakers, compact, editable)
        live = item.position is None or item.preview or item.recording.recording_id == "live"
        self.text_stack.set_visible_child_name("live" if live else "edit")
        if live:
            self._key = None
            self.text_label.set_text(segment.text)
            self._set_preview(segment.text if item.preview else None)
            if item.preview:
                self.text_label.add_css_class("dimmed")
            else:
                self.text_label.remove_css_class("dimmed")
            return
        self._set_preview(None)
        assert item.position is not None
        self._bind_text((item.recording.recording_id, item.position), segment.text)
        self.text_view.set_editable(editable)
        self.text_view.set_cursor_visible(editable)

    def _set_preview(self, text: str | None) -> None:
        """Pulse a caret after realtime preview text; other rows need no frame callback."""
        self._preview_text = text
        self._caret_visible = None
        if text is not None and not self._pulse:
            self._pulse = self.add_tick_callback(self._pulse_caret)

    def _bind_header(self, item: TranscriptItem) -> None:
        self._key = None
        self._set_preview(None)
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
        self.retry_button.set_action_target_value(GLib.Variant("s", item.recording.recording_id))
        self.retry_button.set_action_name("win.retry-recording")
        self.recording_menu_button.set_visible(item.live_summary is None)

    def _bind_meta(
        self,
        item: TranscriptItem,
        segment: Segment,
        show_timestamps: bool,
        show_speakers: bool,
        compact: bool,
        editable: bool,
    ) -> None:
        self.segment_box.set_orientation(
            Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL
        )
        self.meta_box.set_orientation(
            Gtk.Orientation.HORIZONTAL if compact else Gtk.Orientation.VERTICAL
        )
        self.meta_box.set_spacing(8 if compact else 0)
        self.segment_box.set_spacing(0 if compact else 12)
        self.meta_box.set_visible(show_speakers or show_timestamps)
        width = _META_WIDTH_WITH_SPEAKERS if show_speakers else _META_WIDTH_TIMES_ONLY
        self.meta_box.set_size_request(-1 if compact else width, -1)
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
        self.update_speaker_color()
        span = format_timestamp_range(segment.start, segment.end)
        live = item.position is None or item.preview or item.recording.recording_id == "live"
        self.timestamp_button.set_visible(show_timestamps and (bool(span) or not live))
        self.timestamp_label.set_label(span or _("Add Time"))
        self.timestamp_button.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], [span or _("No timestamp")]
        )
        self.timestamp_button.set_sensitive(editable and not live)
        self._segment = segment
        if not self.timestamp_popover.get_visible():
            self._fill_time_rows(segment)

    def _bind_text(self, key: tuple[str, int], text: str) -> None:
        """Show ``text`` without disturbing the caret or typing that is not saved yet."""
        current = self._text()
        if key != self._key:
            self._cancel_commit()
            self._key = key
            self._load(text)
        elif current.strip() == text.strip():
            self._committed = current
        elif current == self._committed:
            # Changed elsewhere (Undo or Redo); nothing typed here is lost.
            self._load(text)

    def set_playing(self, playing: bool) -> None:
        """The header's single button toggles playback rather than overlapping audio."""
        self.play_button.set_icon_name(
            "media-playback-stop-symbolic" if playing else "media-playback-start-symbolic"
        )
        label = _("Stop Playback") if playing else _("Play This Recording")
        self.play_button.set_tooltip_text(label)
        self.play_button.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def edit(self, *, at_end: bool) -> bool:
        """Put the caret in this row's text; False when the row has no editable text."""
        if self._key is None:
            return False
        buffer = self.text_view.get_buffer()
        start, end = buffer.get_bounds()
        buffer.place_cursor(end if at_end else start)
        return self.text_view.grab_focus()

    def flush(self) -> None:
        """Commit pending typing now, e.g. before the row is recycled or Undo runs."""
        self._commit(final=True)

    def _load(self, text: str) -> None:
        self._loading = True
        try:
            self.text_view.get_buffer().set_text(text)
        finally:
            self._loading = False
        self._committed = text

    def _text(self) -> str:
        buffer = self.text_view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False)

    def _on_buffer_changed(self, _buffer: Gtk.TextBuffer) -> None:
        if self._loading or self._key is None:
            return
        self._cancel_commit()
        self._commit_source = GLib.timeout_add(_COMMIT_DELAY_MS, self._on_commit_timeout)

    def _on_commit_timeout(self) -> bool:
        self._commit_source = 0
        self._commit(final=False)
        return False

    def _cancel_commit(self) -> None:
        if self._commit_source:
            GLib.source_remove(self._commit_source)
            self._commit_source = 0

    def _commit(self, *, final: bool) -> None:
        """Emit the typed text; empty text waits for more typing, or reverts when final."""
        self._cancel_commit()
        text = self._text()
        if self._key is None or text == self._committed:
            return
        if validate_segment_edit(text, None, None) is SegmentEditError.EMPTY_TEXT:
            if final:
                self._load(self._committed)
                show_notice(self, _("Segment text cannot be empty"))
            return
        self._committed = text
        self.emit("text-committed", text)

    def _on_text_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
    ) -> bool:
        if self._preedit or not self.text_view.get_editable():
            return False
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not modifiers:
            self._commit(final=True)
            self.emit("navigate", 1)
            return True
        if keyval == Gdk.KEY_Escape and self._text() != self._committed:
            self._cancel_commit()
            self._load(self._committed)
            return True
        return False

    def _on_text_preedit_changed(self, _view: Gtk.TextView, preedit: str) -> None:
        # While an input method composes text, Enter and Escape belong to it.
        self._preedit = bool(preedit)

    def _on_text_keynav_failed(self, _view: Gtk.TextView, direction: Gtk.DirectionType) -> bool:
        match direction:
            case Gtk.DirectionType.UP:
                self.emit("navigate", -1)
                return True
            case Gtk.DirectionType.DOWN:
                self.emit("navigate", 1)
                return True
            case _:
                return False

    def _on_text_focus_leave(self, _controller: Gtk.EventControllerFocus) -> None:
        self._commit(final=True)
        self.emit("focus-left")

    def _fill_time_rows(self, segment: Segment) -> None:
        self._loading_times = True
        try:
            self.start_row.set_text(
                "" if segment.start is None else format_timestamp_input(segment.start)
            )
            self.end_row.set_text(
                "" if segment.end is None else format_timestamp_input(segment.end)
            )
        finally:
            self._loading_times = False
        self._parsed_times()

    def _parsed_times(self) -> tuple[timedelta | None, timedelta | None] | None:
        """The popover's times, or None while either is invalid; marks invalid rows."""
        start_ok = _mark_row(self.start_row)
        end_ok = _mark_row(self.end_row)
        if not (start_ok and end_ok):
            self._show_time_error(_("Use MM:SS or H:MM:SS, with optional decimal seconds"))
            return None
        start = parse_timestamp(self.start_row.get_text())
        end = parse_timestamp(self.end_row.get_text())
        if validate_segment_edit(self._committed, start, end) is SegmentEditError.END_BEFORE_START:
            self.end_row.add_css_class("error")
            message = _("End time must not be earlier than start time")
            self.end_row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [message])
            self._show_time_error(message)
            return None
        self._show_time_error("")
        return start, end

    def _show_time_error(self, message: str) -> None:
        self.times_error_label.set_text(message)
        self.times_error_label.set_visible(bool(message))

    @Gtk.Template.Callback()
    def _on_time_row_changed(self, _row: Gtk.Entry) -> None:
        if not self._loading_times:
            self._parsed_times()

    @Gtk.Template.Callback()
    def _on_time_row_entry_activated(self, _row: Gtk.Entry) -> None:
        if self._parsed_times() is not None:
            self.timestamp_popover.popdown()
            return
        invalid = self.start_row if self.start_row.has_css_class("error") else self.end_row
        invalid.grab_focus()

    @Gtk.Template.Callback()
    def _on_timestamp_popover_closed(self, _popover: Gtk.Popover) -> None:
        times = self._parsed_times()
        segment = self._segment
        if segment is None or times is None:
            if segment is not None:
                self._fill_time_rows(segment)
                show_notice(self, _("Timestamps were not saved because the input is invalid"))
            return
        start, end = times
        if (start, end) != (segment.start, segment.end):
            self.emit("times-committed", _to_ms(start), _to_ms(end))

    def update_speaker_color(self) -> None:
        """Follow a switch between the light and dark style."""
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


def _mark_row(row: Gtk.Entry) -> bool:
    """Whether ``row`` holds a parseable timestamp; marks the row when it does not."""
    try:
        parse_timestamp(row.get_text())
    except TimestampParseError:
        row.add_css_class("error")
        row.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("Use MM:SS or H:MM:SS, with optional decimal seconds")],
        )
        return False
    row.remove_css_class("error")
    row.update_property([Gtk.AccessibleProperty.DESCRIPTION], [""])
    return True


def _to_ms(value: timedelta | None) -> int:
    return NO_TIME if value is None else round(value / timedelta(milliseconds=1))


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
    delete = Gio.MenuItem()
    delete.set_label(_("Delete Recording"))
    delete.set_action_and_target_value("win.delete-recording", GLib.Variant("s", recording_id))
    menu.append_item(delete)
    return menu
