"""Await GTK file choosers as coroutines; each returns None when the user dismisses it."""

import asyncio
from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio, GLib, Gtk

from kikoe.i18n import _
from kikoe.ui.tasks import show_notice


async def run_file_dialog(
    parent: Gtk.Window,
    start: Callable[[Gtk.Window, None, Callable[[Gtk.FileDialog, Gio.AsyncResult], None]], None],
    finish: Callable[[Gio.AsyncResult], Gio.File | None],
    not_local: str,
) -> Path | None:
    """Await a file dialog; None if it was dismissed or chose a non-local file."""
    future: asyncio.Future[Gio.File | None] = asyncio.get_running_loop().create_future()

    def done(_dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            future.set_result(finish(result))
        except GLib.Error as error:
            if error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                future.set_result(None)
            else:
                future.set_exception(error)

    start(parent, None, done)
    file = await future
    if file is None:
        return None
    path = file.get_path()
    if path is None:
        show_notice(parent, not_local)
        return None
    return Path(path)


async def pick_save_path(
    parent: Gtk.Window, title: str, initial_name: str, filter_label: str, pattern: str
) -> Path | None:
    """Ask where to save."""
    target = Gtk.FileFilter(name=filter_label)
    target.add_pattern(pattern)
    everything = Gtk.FileFilter(name=_("All Files"))
    everything.add_pattern("*")
    filters: Gio.ListStore[Gtk.FileFilter] = Gio.ListStore(item_type=Gtk.FileFilter)
    filters.append(target)
    filters.append(everything)
    dialog = Gtk.FileDialog(
        title=title, filters=filters, default_filter=target, initial_name=initial_name
    )
    return await run_file_dialog(
        parent, dialog.save, dialog.save_finish, _("Only locations on this computer can be used.")
    )


async def pick_audio_file(parent: Gtk.Window) -> Path | None:
    """Ask for an audio or video file to import."""
    media = Gtk.FileFilter(name=_("Audio and Video"))
    media.add_mime_type("audio/*")
    media.add_mime_type("video/*")
    filters: Gio.ListStore[Gtk.FileFilter] = Gio.ListStore(item_type=Gtk.FileFilter)
    filters.append(media)
    dialog = Gtk.FileDialog(title=_("Import Audio"), filters=filters, default_filter=media)
    return await run_file_dialog(
        parent, dialog.open, dialog.open_finish, _("Only files on this computer can be imported.")
    )
