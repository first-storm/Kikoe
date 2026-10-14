"""Ask whether unfinished recordings from an earlier session should be recovered."""

import asyncio

from gi.repository import Adw, Gtk

from kikoe.i18n import _


async def confirm_recovery(parent: Gtk.Widget) -> bool:
    """Show the prompt and wait; closing it counts as “Later”."""
    dialog = Adw.AlertDialog(
        heading=_("Recover Recordings?"),
        body=_(
            "Kikoe found unfinished recordings. Recover their audio now, then "
            "choose a model to transcribe them."
        ),
    )
    dialog.add_response("later", _("_Later"))
    dialog.add_response("recover", _("_Recover"))
    dialog.set_response_appearance("recover", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("recover")
    dialog.set_close_response("later")
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
    return response == "recover"
