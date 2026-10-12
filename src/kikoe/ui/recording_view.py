"""The batch-mode recording placeholder from the second design screen."""

from gi.repository import Gtk

from kikoe.core.recording import CaptureState
from kikoe.i18n import _
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("recording_view"))
class RecordingView(Gtk.Box):
    """Show the frozen model choice while the microphone collects audio."""

    __gtype_name__ = "KikoeRecordingView"

    title_label: Gtk.Label = Gtk.Template.Child()
    meta_label: Gtk.Label = Gtk.Template.Child()
    status_label: Gtk.Label = Gtk.Template.Child()
    description_label: Gtk.Label = Gtk.Template.Child()

    def configure(self, number: int, summary: str, model: str) -> None:
        self.title_label.set_label(_("Recording {number}").format(number=number))
        self.meta_label.set_label(_("In progress · {summary}").format(summary=summary))
        self.status_label.set_label(_("Recording"))
        self.description_label.set_label(
            _(
                "Kikoe will upload the audio to {model} and transcribe it automatically "
                "after you stop. The text will appear here."
            ).format(model=model)
        )

    def set_state(self, state: CaptureState) -> None:
        self.status_label.set_label(_("Paused") if state is CaptureState.PAUSED else _("Recording"))
