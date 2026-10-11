"""Helpers for starting background work from widgets and reporting its failures."""

import asyncio
import logging
from collections.abc import Coroutine

from gi.repository import Adw, Gio, Gtk

from kikoe.i18n import _
from kikoe.media.ffmpeg import FFmpegError
from kikoe.providers.base import TranscriptionError
from kikoe.storage.library import LibraryError, NoteNotFoundError
from kikoe.storage.providers import ProviderNotFoundError
from kikoe.storage.secrets import SecretStoreError
from kikoe.storage.settings import SettingsError

log = logging.getLogger(__name__)

USER_FACING_ERRORS = (
    SettingsError,
    SecretStoreError,
    ProviderNotFoundError,
    LibraryError,
    NoteNotFoundError,
    TranscriptionError,
    FFmpegError,
)
"""Exceptions whose message is written for the user and can be shown as is."""


def show_error(parent: Gtk.Widget, message: str) -> None:
    dialog = Adw.AlertDialog(heading=_("Something Went Wrong"), body=message)
    dialog.add_response("close", _("_Close"))
    dialog.present(parent)


def run_task[T](coroutine: Coroutine[None, None, T], parent: Gtk.Widget) -> asyncio.Task[T | None]:
    """Run ``coroutine`` on the main loop; failures with user-readable messages open a dialog.

    Keep the returned task if the work may need cancelling.
    """
    application = Gio.Application.get_default()
    if application is None:
        raise RuntimeError("No running application")
    return application.create_asyncio_task(_report_errors(coroutine, parent))  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]  # PyGObject 3.58 API, untyped in the stubs


async def _report_errors[T](coroutine: Coroutine[None, None, T], parent: Gtk.Widget) -> T | None:
    try:
        return await coroutine
    except USER_FACING_ERRORS as error:
        log.warning("Background task failed: %s", error)
        show_error(parent, str(error))
        return None
