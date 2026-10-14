"""One reversible recognition-label assignment in the matching dialog."""

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from kikoe.core.formatting import SPEAKER_COLOR_COUNT
from kikoe.core.models import Note, Recording, Speaker, SpeakerBinding
from kikoe.core.speakers import sample_range, sample_segment, speaker_numbers
from kikoe.i18n import _
from kikoe.ui.templates import ui_file


def speaker_label(note: Note, speaker_id: str) -> str:
    """Keep fallback labels translatable and numbered like the transcript."""
    person = note.speaker(speaker_id)
    if person is None:
        return ""
    number = speaker_numbers(note).get(speaker_id, person.ordinal)
    return person.name or _("Speaker {number}").format(number=number)


def color_widget(widget: Gtk.Widget, ordinal: int, *, title_only: bool = False) -> None:
    """Use the same theme palette in the transcript and matching controls."""
    for slot in range(SPEAKER_COLOR_COUNT):
        widget.remove_css_class(f"speaker-{slot}")
        widget.remove_css_class(f"speaker-{slot}-dark")
        widget.remove_css_class(f"speaker-title-{slot}")
        widget.remove_css_class(f"speaker-title-{slot}-dark")
    if ordinal <= 0:
        return
    slot = (ordinal - 1) % SPEAKER_COLOR_COUNT
    dark = Adw.StyleManager.get_default().get_dark()
    prefix = "speaker-title" if title_only else "speaker"
    widget.add_css_class(f"{prefix}-{slot}-dark" if dark else f"{prefix}-{slot}")


@Gtk.Template(filename=ui_file("speaker_row"))
class SpeakerRow(Adw.ActionRow):
    """Sample playback plus a colored selection, following the HTML row order."""

    __gtype_name__ = "KikoeSpeakerRow"
    play_button: Gtk.Button = Gtk.Template.Child()
    choice_button: Gtk.MenuButton = Gtk.Template.Child()
    choice_popover: Gtk.Popover = Gtk.Template.Child()
    choices_list: Gtk.ListBox = Gtk.Template.Child()
    keep_check: Gtk.Image = Gtk.Template.Child()
    target_label: Gtk.Label = Gtk.Template.Child()

    def __init__(
        self,
        note: Note,
        recording: Recording,
        binding: SpeakerBinding,
        candidates: tuple[Speaker, ...],
    ) -> None:
        super().__init__()
        self.binding = binding
        self._note = note
        self.selected: str | None = (
            binding.speaker_id
            if binding.confirmed and binding.speaker_id != binding.original_id
            else None
        )
        name = speaker_label(note, binding.original_id)
        self.set_title(name)
        self.set_use_markup(False)
        sample = sample_segment(recording, binding.source_id)
        self.set_subtitle(
            _("“{text}”").format(text=sample.text) if sample else _("No sample available")
        )
        self.set_subtitle_lines(1)
        self.play_button.set_tooltip_text(_("Play a sample from “{name}”").format(name=name))
        self.choice_button.set_tooltip_text(_("Who is {name}?").format(name=name))
        span = sample_range(recording, sample) if sample is not None else None
        self.play_button.set_sensitive(span is not None and recording.audio_path is not None)
        if span is None:
            self.play_button.set_tooltip_text(_("Sample playback needs reliable timestamps."))
        else:
            self.play_button.set_action_target_value(
                GLib.Variant(
                    "(sdd)",
                    (recording.recording_id, span[0].total_seconds(), span[1].total_seconds()),
                )
            )
            self.play_button.set_action_name("win.play-sample")
        self._items: Gio.ListStore[SpeakerItem] = Gio.ListStore(item_type=SpeakerItem)
        self._items.splice(
            0,
            0,
            [
                SpeakerItem(person)
                for person in candidates
                if person.speaker_id != binding.original_id
            ],
        )
        self.choices_list.bind_model(self._items, self._create_choice)
        self._color_title()
        self._update_choice()
        self._dark = Adw.StyleManager.get_default().get_dark()
        self.add_tick_callback(self._on_tick)

    def _on_tick(self, _widget: Gtk.Widget, _clock: object) -> bool:
        dark = Adw.StyleManager.get_default().get_dark()
        if dark != self._dark:
            self._dark = dark
            self._color_title()
            self._update_choice()
        return True

    def _color_title(self) -> None:
        person = self._note.speaker(self.binding.original_id)
        color_widget(self, person.ordinal if person else 0, title_only=True)

    def _create_choice(self, item: object) -> Gtk.Widget:
        assert isinstance(item, SpeakerItem)
        person = item.person
        button = Gtk.Button(css_classes=["flat"])
        box = Gtk.Box(spacing=10)
        label = Gtk.Label(
            label=speaker_label(self._note, person.speaker_id), xalign=0, hexpand=True
        )
        color_widget(label, person.ordinal)
        box.append(label)
        box.append(
            Gtk.Image(
                icon_name="object-select-symbolic", visible=self.selected == person.speaker_id
            )
        )
        button.set_child(box)
        button.connect("clicked", self._on_choice_button_clicked, person.speaker_id)
        return button

    def _on_choice_button_clicked(self, _button: Gtk.Button, speaker_id: str) -> None:
        self.selected = speaker_id
        self._update_choice()
        self.choice_popover.popdown()

    @Gtk.Template.Callback()
    def _on_keep_button_clicked(self, _button: Gtk.Button) -> None:
        self.selected = None
        self._update_choice()
        self.choice_popover.popdown()

    def _update_choice(self) -> None:
        person = self._note.speaker(self.selected)
        self.target_label.set_label(
            speaker_label(self._note, person.speaker_id) if person else _("New Speaker")
        )
        color_widget(self.target_label, person.ordinal if person else 0)
        self.keep_check.set_visible(self.selected is None)
        self.choices_list.bind_model(self._items, self._create_choice)


class SpeakerItem(GObject.Object):
    """A model item carrying a frozen person."""

    def __init__(self, person: Speaker) -> None:
        super().__init__()
        self.person = person
