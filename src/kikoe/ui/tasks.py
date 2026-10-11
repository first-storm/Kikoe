"""Helpers for starting background work from widgets and reporting its failures."""

import logging
from collections.abc import Coroutine

from gi.repository import Adw, Gio, Gtk

from kikoe.i18n import _
from kikoe.storage.providers import ProviderNotFoundError
from kikoe.storage.secrets import SecretStoreError
from kikoe.storage.settings import SettingsError

log = logging.getLogger(__name__)

_USER_FACING_ERRORS = (SettingsError, SecretStoreError, ProviderNotFoundError)


def show_error(parent: Gtk.Widget, message: str) -> None:
    dialog = Adw.AlertDialog(heading=_("Something Went Wrong"), body=message)
    dialog.add_response("close", _("_Close"))
    dialog.present(parent)


def run_task(coroutine: Coroutine[None, None, None], parent: Gtk.Widget) -> None:
    """Run ``coroutine`` on the main loop; failures with user-readable messages open a dialog."""
    application = Gio.Application.get_default()
    if application is None:
        raise RuntimeError("No running application")
    application.create_asyncio_task(_report_errors(coroutine, parent))  # pyright: ignore[reportUnknownMemberType]  # PyGObject 3.58 API, untyped in the stubs


async def _report_errors(coroutine: Coroutine[None, None, None], parent: Gtk.Widget) -> None:
    try:
        await coroutine
    except _USER_FACING_ERRORS as error:
        log.warning("Background task failed: %s", error)
        show_error(parent, str(error))
