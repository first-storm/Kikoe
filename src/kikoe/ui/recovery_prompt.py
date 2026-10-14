"""Ask whether unfinished recordings from an earlier session should be recovered."""

import asyncio

from gi.repository import Adw, Gtk

from kikoe.i18n import _
from kikoe.ui.dialogs import alert_dialog


async def confirm_recovery(parent: Gtk.Widget) -> bool:
    """Show the prompt and wait; closing it counts as “Later”."""
    dialog = alert_dialog(
        _("Recover Recordings?"),
        _(
            "Kikoe found unfinished recordings. Recover their audio now, then "
            "choose a model to transcribe them."
        ),
        _("_Later"),
        _("_Recover"),
    )
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    def respond(_dialog: Adw.AlertDialog, response: str) -> None:
        if not future.done():
            future.set_result(response)

    dialog.connect("response", respond)
    dialog.present(parent)
    try:
        response = await future
    finally:
        dialog.close()
    return response == "confirm"
