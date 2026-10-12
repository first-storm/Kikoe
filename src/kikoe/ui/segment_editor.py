"""Edit one transcript segment's text and timestamps."""

from datetime import timedelta

from gi.repository import Adw, Gtk

from kikoe.core.formatting import (
    TimestampParseError,
    format_timestamp_input,
    parse_timestamp,
)
from kikoe.core.models import Segment
from kikoe.core.validation import SegmentEditError, validate_segment_edit
from kikoe.i18n import _
from kikoe.storage.notes import NoteRepository
from kikoe.ui.dialogs import confirm_discard
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("segment_editor"))
class SegmentEditor(Adw.Dialog):
    """Manual fixes apply to this segment only and survive re-transcription."""

    __gtype_name__ = "KikoeSegmentEditor"
    text_view: Gtk.TextView = Gtk.Template.Child()
    start_row: Adw.EntryRow = Gtk.Template.Child()
    end_row: Adw.EntryRow = Gtk.Template.Child()
    error_label: Gtk.Label = Gtk.Template.Child()
    save_button: Gtk.Button = Gtk.Template.Child()

    def __init__(
        self,
        notes: NoteRepository,
        note_id: str,
        recording_id: str,
        position: int,
        segment: Segment,
    ) -> None:
        super().__init__()
        self._notes = notes
        self._note_id = note_id
        self._recording_id = recording_id
        self._position = position
        self.text_view.get_buffer().set_text(segment.text)
        self.text_view.get_buffer().connect("changed", self._on_text_changed)
        if segment.start is not None:
            self.start_row.set_text(format_timestamp_input(segment.start))
        if segment.end is not None:
            self.end_row.set_text(format_timestamp_input(segment.end))
        self._validate()
        initial = self._fields()
        confirm_discard(self, lambda: self._fields() != initial)

    @Gtk.Template.Callback()
    def _on_start_row_changed(self, _row: Adw.EntryRow) -> None:
        self._validate()

    @Gtk.Template.Callback()
    def _on_end_row_changed(self, _row: Adw.EntryRow) -> None:
        self._validate()

    def _on_text_changed(self, _buffer: Gtk.TextBuffer) -> None:
        self._validate()

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.force_close()

    @Gtk.Template.Callback()
    def _on_save_button_clicked(self, _button: Gtk.Button) -> None:
        self.save_button.set_sensitive(False)
        run_task(self._save(), self)

    async def _save(self) -> None:
        try:
            parsed = self._parse()
            if parsed is None:
                return
            text, start, end = parsed
            await self._notes.update_segment(
                self._note_id,
                self._recording_id,
                self._position,
                text=text,
                start_ms=_to_ms(start),
                end_ms=_to_ms(end),
            )
            self.force_close()
        finally:
            self._validate()

    def _fields(self) -> tuple[str, str, str]:
        return self._text(), self.start_row.get_text(), self.end_row.get_text()

    def _text(self) -> str:
        buffer = self.text_view.get_buffer()
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False)

    def _parse(self) -> tuple[str, timedelta | None, timedelta | None] | None:
        """The current field values, or None while any of them is invalid."""
        try:
            start = parse_timestamp(self.start_row.get_text())
            end = parse_timestamp(self.end_row.get_text())
        except TimestampParseError:
            return None
        text = self._text()
        if validate_segment_edit(text, start, end) is not None:
            return None
        return text.strip(), start, end

    def _validate(self) -> None:
        start_ok = _parse_field(self.start_row)
        end_ok = _parse_field(self.end_row)
        message: str | None = None
        if not start_ok:
            message = _("Enter a valid start time, or leave it empty.")
        elif not end_ok:
            message = _("Enter a valid end time, or leave it empty.")
        else:
            start = parse_timestamp(self.start_row.get_text())
            end = parse_timestamp(self.end_row.get_text())
            match validate_segment_edit(self._text(), start, end):
                case SegmentEditError.EMPTY_TEXT:
                    message = _("Enter transcript text.")
                case SegmentEditError.END_BEFORE_START:
                    message = _("The end time must be after the start time.")
                case None:
                    message = None
        if message is None:
            self.error_label.set_visible(False)
        else:
            self.error_label.set_label(message)
            self.error_label.set_visible(True)
        self.save_button.set_sensitive(message is None)


def _parse_field(row: Adw.EntryRow) -> bool:
    """Whether ``row`` holds a parseable timestamp; marks the row when it does not."""
    try:
        parse_timestamp(row.get_text())
    except TimestampParseError:
        row.add_css_class("error")
        return False
    row.remove_css_class("error")
    return True


def _to_ms(value: timedelta | None) -> int | None:
    return None if value is None else round(value / timedelta(milliseconds=1))
