"""Helpers for starting background work from widgets and reporting its failures."""

import asyncio
import logging
from collections.abc import Coroutine
from typing import Protocol, runtime_checkable

from gi.repository import Adw, Gio, Gtk

from kikoe.i18n import _
from kikoe.services.errors import USER_FACING_ERRORS

log = logging.getLogger(__name__)


def show_error(parent: Gtk.Widget, message: str, heading: str | None = None) -> None:
    """Block on a failure the user must read; use ``show_notice`` for small, self-evident ones."""
    dialog = Adw.AlertDialog(heading=heading or _("Something Went Wrong"), body=message)
    dialog.add_response("close", _("_Close"))
    dialog.present(parent)


@runtime_checkable
class ToastHost(Protocol):
    """A window that can show toasts; ``Window`` satisfies it without importing it here."""

    def add_toast(self, toast: Adw.Toast) -> None: ...


def show_notice(parent: Gtk.Widget, message: str) -> None:
    """Report a minor, self-explanatory problem in a toast instead of a modal dialog."""
    window = parent.get_root()
    if isinstance(window, ToastHost):
        window.add_toast(Adw.Toast(title=message))
    else:
        show_error(parent, message)


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
    except Exception:
        log.exception("Unexpected background failure")
        show_error(parent, _("An unexpected error occurred. See the local log for details."))
        return None
