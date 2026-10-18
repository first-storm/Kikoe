"""The window's “Show Timestamps” and “Show Speakers” toggles."""

from collections.abc import Callable

from gi.repository import Adw, Gio, GLib

from kikoe.core.models import DisplayOptions, Note
from kikoe.storage.notes import NoteRepository
from kikoe.ui.tasks import run_task

_NAMES = ("show-timestamps", "show-speakers")


class DisplayActions:
    """Adds the toggle actions to a window and saves each choice for the open note."""

    def __init__(
        self, window: Adw.ApplicationWindow, notes: NoteRepository, note: Callable[[], Note | None]
    ) -> None:
        self._window = window
        self._notes = notes
        self._note = note
        for name in _NAMES:
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(False))
            action.connect("activate", self._on_activate)
            window.add_action(action)

    def show(self, options: DisplayOptions) -> None:
        """Make the toggles match ``options``."""
        self._set("show-timestamps", options.show_timestamps)
        self._set("show-speakers", options.show_speakers)

    def _get(self, name: str) -> bool:
        action = self._window.lookup_action(name)
        return action is not None and action.get_state() == GLib.Variant.new_boolean(True)

    def _set(self, name: str, value: bool) -> None:
        action = self._window.lookup_action(name)
        if isinstance(action, Gio.SimpleAction):
            action.set_state(GLib.Variant.new_boolean(value))

    def _on_activate(self, action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        note = self._note()
        if note is None:
            return
        self._set(action.get_name(), not self._get(action.get_name()))
        options = DisplayOptions(self._get("show-timestamps"), self._get("show-speakers"))
        # Not compared with the open note: it still shows the previous choice until the
        # save is reloaded, so a quick change back would look like no change at all.
        run_task(self._notes.set_display_options(note.note_id, options), self._window)
