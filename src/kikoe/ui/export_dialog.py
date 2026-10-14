"""Dialog for choosing export format and options when exporting a note."""

from collections.abc import Callable

from gi.repository import Adw, GObject, Gtk

from kikoe.core.export import ExportFormat
from kikoe.core.models import DisplayOptions, Note
from kikoe.core.transcript_rows import transcript_columns
from kikoe.i18n import _
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("export_dialog"))
class ExportDialog(Adw.Dialog):
    """Presents format choices and options before invoking the save file picker."""

    __gtype_name__ = "KikoeExportDialog"

    md_radio: Gtk.CheckButton = Gtk.Template.Child()
    srt_row: Adw.ActionRow = Gtk.Template.Child()
    srt_radio: Gtk.CheckButton = Gtk.Template.Child()
    vtt_row: Adw.ActionRow = Gtk.Template.Child()
    vtt_radio: Gtk.CheckButton = Gtk.Template.Child()
    timestamps_row: Adw.SwitchRow = Gtk.Template.Child()
    speakers_row: Adw.SwitchRow = Gtk.Template.Child()

    def __init__(
        self,
        note: Note,
        on_export: Callable[[ExportFormat, DisplayOptions], None],
    ) -> None:
        super().__init__()
        self._on_export = on_export
        self._has_timestamps, has_speakers = transcript_columns(
            s for r in note.recordings for s in r.segments
        )
        self._user_show_timestamps = note.display.show_timestamps

        if has_speakers:
            self.speakers_row.set_active(note.display.show_speakers)
        else:
            self.speakers_row.set_active(False)
            self.speakers_row.set_sensitive(False)
            self.speakers_row.set_subtitle(_("This note has no speakers"))

        if not self._has_timestamps:
            unavail = _("{extension} · Unavailable because this note has no timestamps")
            self.srt_row.set_sensitive(False)
            self.srt_row.set_subtitle(unavail.format(extension=".srt"))
            self.vtt_row.set_sensitive(False)
            self.vtt_row.set_subtitle(unavail.format(extension=".vtt"))

        self._update_options_state()

    @Gtk.Template.Callback()
    def _on_format_toggled(self, radio: Gtk.CheckButton) -> None:
        if radio.get_active():
            self._update_options_state()

    @Gtk.Template.Callback()
    def _on_timestamps_row_notify_active(
        self, _row: Adw.SwitchRow, _param: GObject.ParamSpec
    ) -> None:
        if not self._selected_format().is_subtitle and self._has_timestamps:
            self._user_show_timestamps = self.timestamps_row.get_active()

    def _selected_format(self) -> ExportFormat:
        if self.md_radio.get_active():
            return ExportFormat.MARKDOWN
        if self.srt_radio.get_active():
            return ExportFormat.SRT
        if self.vtt_radio.get_active():
            return ExportFormat.WEBVTT
        return ExportFormat.TEXT

    def _update_options_state(self) -> None:
        fmt = self._selected_format()
        if fmt.is_subtitle:
            self.timestamps_row.set_active(True)
            self.timestamps_row.set_sensitive(False)
            self.timestamps_row.set_subtitle(_("Always included in subtitle formats"))
        elif not self._has_timestamps:
            self.timestamps_row.set_active(False)
            self.timestamps_row.set_sensitive(False)
            self.timestamps_row.set_subtitle(_("This note has no timestamps"))
        else:
            self.timestamps_row.set_sensitive(True)
            self.timestamps_row.set_active(self._user_show_timestamps)
            self.timestamps_row.set_subtitle("")

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.close()

    @Gtk.Template.Callback()
    def _on_export_button_clicked(self, _button: Gtk.Button) -> None:
        fmt = self._selected_format()
        options = DisplayOptions(
            show_timestamps=self.timestamps_row.get_active(),
            show_speakers=self.speakers_row.get_active(),
        )
        self.force_close()
        self._on_export(fmt, options)
