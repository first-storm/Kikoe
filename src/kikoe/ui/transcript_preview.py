"""Lightweight transcript text; allocate an editor only when editing is requested."""

from collections.abc import Callable
from weakref import finalize, ref

from gi.repository import Adw, GLib, Gtk, Pango

from kikoe.core.formatting import SPEAKER_COLOR_COUNT, format_timestamp_range
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem
from kikoe.ui.templates import ui_file
from kikoe.ui.transcript_row import TranscriptRow


@Gtk.Template(filename=ui_file("transcript_preview"))
class TranscriptPreview(Gtk.Box):
    """Keep ordinary reading free of text buffers, entry fields and popovers."""

    __gtype_name__ = "KikoeTranscriptPreview"
    meta_box: Gtk.Box = Gtk.Template.Child()
    speaker_button: Gtk.Button = Gtk.Template.Child()
    speaker_label: Gtk.Label = Gtk.Template.Child()
    timestamp_button: Gtk.Button = Gtk.Template.Child()
    timestamp_label: Gtk.Label = Gtk.Template.Child()
    text_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self, ensure_row: Callable[[], TranscriptRow] | None = None) -> None:
        super().__init__()
        self._ensure_row = ensure_row
        self._editable = True
        self._color_slot: int | None = None
        style = Adw.StyleManager.get_default()
        preview_ref = ref(self)

        def dark_changed(_style: Adw.StyleManager, _property: object) -> None:
            preview = preview_ref()
            if preview is not None:
                preview._update_speaker_color()

        handler = style.connect("notify::dark", dark_changed)
        finalize(self, style.disconnect, handler)
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_label_pressed)
        self.text_label.add_controller(click)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self._on_label_focus_enter)
        self.text_label.add_controller(focus)

    def set_editor_factory(self, factory: Callable[[], TranscriptRow] | None) -> None:
        """Reassign a recycled reading row to its current cell."""
        self._ensure_row = factory

    def _editor(self) -> TranscriptRow:
        if self._ensure_row is None:
            raise RuntimeError("The transcript preview is not bound to a cell")
        return self._ensure_row()

    def bind(
        self,
        item: TranscriptItem,
        *,
        show_timestamps: bool,
        show_speakers: bool,
        compact: bool,
        editable: bool,
    ) -> None:
        """Use the same text, metadata and actions as the in-place editor."""
        segment = item.segment
        assert segment is not None
        self._editable = editable
        self.set_orientation(Gtk.Orientation.VERTICAL if compact else Gtk.Orientation.HORIZONTAL)
        self.meta_box.set_orientation(
            Gtk.Orientation.HORIZONTAL if compact else Gtk.Orientation.VERTICAL
        )
        self.set_spacing(0 if compact else 12)
        self.meta_box.set_spacing(8 if compact else 0)
        self.meta_box.set_visible(show_speakers or show_timestamps)
        self.meta_box.set_size_request(-1 if compact else 132 if show_speakers else 96, -1)
        self.speaker_button.set_visible(show_speakers and item.speaker_name is not None)
        self.speaker_button.set_action_name(None)
        self.speaker_button.set_action_target_value(
            GLib.Variant("(ss)", (item.recording.recording_id, segment.speaker_id or ""))
        )
        self.speaker_button.set_action_name("win.edit-speaker")
        self.speaker_label.set_text(item.speaker_name or "")
        self._color_slot = item.color_slot
        self._update_speaker_color()
        span = format_timestamp_range(segment.start, segment.end)
        self.timestamp_button.set_visible(show_timestamps)
        self.timestamp_button.set_sensitive(editable)
        self.timestamp_label.set_text(span or _("Add Time"))
        self.timestamp_button.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION], [span or _("No timestamp")]
        )
        self.text_label.set_text(segment.text)
        self.text_label.set_focusable(editable)

    def _update_speaker_color(self) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        for slot in range(SPEAKER_COLOR_COUNT):
            for suffix in ("", "-dark"):
                self.speaker_label.remove_css_class(f"speaker-{slot}{suffix}")
        if self._color_slot is not None:
            self.speaker_label.add_css_class(f"speaker-{self._color_slot}{'-dark' if dark else ''}")

    @Gtk.Template.Callback()
    def _on_timestamp_button_clicked(self, _button: Gtk.Button) -> None:
        self._editor().timestamp_popover.popup()

    def _on_label_focus_enter(self, _controller: Gtk.EventControllerFocus) -> None:
        if self._editable:
            self._editor().edit(at_end=False)

    def _on_label_pressed(
        self, _gesture: Gtk.GestureClick, _presses: int, x: float, y: float
    ) -> None:
        if not self._editable:
            return
        layout = self.text_label.get_layout()
        offset_x, offset_y = self.text_label.get_layout_offsets()
        _inside, byte_index, trailing = layout.xy_to_index(
            round((x - offset_x) * Pango.SCALE), round((y - offset_y) * Pango.SCALE)
        )
        text = self.text_label.get_text()
        position = len(text.encode("utf-8")[:byte_index].decode("utf-8")) + trailing
        row = self._editor()
        row.edit(at_end=False)
        buffer = row.text_view.get_buffer()
        buffer.place_cursor(buffer.get_iter_at_offset(position))
