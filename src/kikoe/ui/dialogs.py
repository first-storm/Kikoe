"""Shared dialog behavior: confirmations before destroying data or unsaved input."""

from collections.abc import Callable

from gi.repository import Adw, Gtk

from kikoe.i18n import _


def alert_dialog(
    heading: str,
    body: str,
    cancel: str,
    confirm: str,
    on_confirm: Callable[[], object] = lambda: None,
    *,
    destructive: bool = False,
) -> Adw.AlertDialog:
    """Answers "cancel" (also on close) or "confirm"; Enter confirms unless that destroys."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", cancel)
    dialog.add_response("confirm", confirm)
    dialog.set_response_appearance(
        "confirm",
        Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED,
    )
    dialog.set_default_response("cancel" if destructive else "confirm")
    dialog.set_close_response("cancel")

    def on_response(_dialog: Adw.AlertDialog, response: str) -> None:
        if response == "confirm":
            on_confirm()

    dialog.connect("response", on_response)
    return dialog


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
        alert_dialog(
            _("Discard Changes?"),
            _("Your changes have not been saved and will be lost."),
            _("_Keep Editing"),
            _("_Discard"),
            dialog.force_close,
            destructive=True,
        ).present(dialog)

    dialog.connect("close-attempt", on_close_attempt)


def confirm_destructive(
    parent: Gtk.Widget, heading: str, body: str, label: str, on_confirm: Callable[[], object]
) -> None:
    """Ask before an irreversible action."""
    alert_dialog(heading, body, _("_Cancel"), label, on_confirm, destructive=True).present(parent)
