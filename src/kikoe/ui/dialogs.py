"""Shared dialog behavior: confirmations before destroying data or unsaved input."""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from kikoe.i18n import _


def confirm_discard(dialog: Adw.Dialog, is_dirty: Callable[[], bool]) -> None:
    """Make Escape, the backdrop and swipes ask first when ``is_dirty``.

    Close the dialog from code with ``force_close()``: ``can-close`` is permanently off, so
    ``close()`` would only emit ``close-attempt`` again.
    """
    dialog.set_can_close(False)

    def on_close_attempt(_dialog: Adw.Dialog) -> None:
        if not is_dirty():
            dialog.force_close()
            return
        alert = Adw.AlertDialog(
            heading=_("Discard Changes?"),
            body=_("Your changes have not been saved and will be lost."),
        )
        alert.add_response("keep", _("_Keep Editing"))
        alert.add_response("discard", _("_Discard"))
        alert.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response("keep")
        alert.set_close_response("keep")

        def on_response(_alert: Adw.AlertDialog, response: str) -> None:
            if response == "discard":
                dialog.force_close()

        alert.connect("response", on_response)
        alert.present(dialog)

    dialog.connect("close-attempt", on_close_attempt)


def confirm_destructive(
    parent: Gtk.Widget, heading: str, body: str, label: str, on_confirm: Callable[[], object]
) -> None:
    """Ask before an irreversible action; Cancel is the default so Enter is safe."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", _("_Cancel"))
    dialog.add_response("confirm", label)
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")

    def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response == "confirm":
            on_confirm()

    dialog.connect("response", on_response)
    dialog.present(parent)
