"""Export, copy and save-as actions for the note shown in the main window."""

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable

from gi.repository import Gio, GLib, Gtk

from kikoe.core.export import ExportFormat, export_filename, export_note
from kikoe.core.models import DisplayOptions, Recording
from kikoe.i18n import _
from kikoe.ui.export_dialog import ExportDialog
from kikoe.ui.file_dialogs import pick_save_path
from kikoe.ui.labels import export_format_label
from kikoe.ui.tasks import run_task, show_error, show_notice
from kikoe.ui.window_host import WindowHost

log = logging.getLogger(__name__)


class ExportController:
    """Owns the export flows; it reads the note from the window each time it is needed."""

    def __init__(
        self,
        window: Gtk.Window,
        host: WindowHost,
        flush_edits: Callable[[], None],
        wait_saved: Callable[[], Awaitable[None]],
    ) -> None:
        self._window = window
        self._host = host
        self._flush_edits = flush_edits
        self._wait_saved = wait_saved

    def on_export(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._flush_edits()
        run_task(self._present_export(), self._window)

    async def _present_export(self) -> None:
        await self._wait_saved()
        note = self._host.note
        if note is None:
            return
        ExportDialog(note, self._on_export_dialog_submitted).present(self._window)

    def _on_export_dialog_submitted(self, fmt: ExportFormat, options: DisplayOptions) -> None:
        run_task(self._export_note(fmt, options), self._window)

    def on_copy_all_text(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._flush_edits()
        run_task(self._copy_all_text(), self._window)

    async def _copy_all_text(self) -> None:
        await self._wait_saved()
        note = self._host.note
        if note is None:
            return
        self._window.get_clipboard().set(export_note(note, ExportFormat.TEXT, note.display))
        self._host.toast(_("Copied to clipboard"))

    async def _export_note(self, fmt: ExportFormat, options: DisplayOptions) -> None:
        note = self._host.note
        if note is None:
            return
        content = export_note(note, fmt, options)
        target = await pick_save_path(
            self._window,
            _("Export Note"),
            export_filename(note.title, fmt.value),
            export_format_label(fmt),
            f"*.{fmt.value}",
        )
        if target is None:
            return
        try:
            await asyncio.to_thread(target.write_text, content, encoding="utf-8")
        except OSError as error:
            log.warning("Export of note %s failed: %s", note.note_id, error)
            message = _("Could not save “{name}”: {error}").format(name=target.name, error=error)
            show_error(self._window, message)
            return
        self._host.toast(_("Exported “{name}”").format(name=target.name))

    def on_export_audio(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        note = self._host.note
        if note is None or param is None:
            return
        recording = note.recording(param.get_string())
        if recording is None:
            self._host.toast(_("This recording is no longer available."))
            return
        run_task(self._export_audio(note.recordings.index(recording) + 1, recording), self._window)

    async def _export_audio(self, number: int, recording: Recording) -> None:
        note = self._host.note
        source = recording.audio_path
        if note is None or source is None:
            return
        suffix = source.suffix or ".ogg"
        target = await pick_save_path(
            self._window,
            _("Export Original Audio"),
            export_filename(f"{note.title} - Recording {number}", suffix.lstrip(".")),
            _("Audio Files"),
            f"*{suffix}",
        )
        if target is None:
            return
        try:
            await asyncio.to_thread(shutil.copyfile, source, target)
        except FileNotFoundError:
            log.warning(
                "Original audio for recording %s is missing: %s", recording.recording_id, source
            )
            show_notice(self._window, _("The original audio is not available."))
            return
        except OSError as error:
            log.warning("Audio export of recording %s failed: %s", recording.recording_id, error)
            message = _("Could not save “{name}”: {error}").format(name=target.name, error=error)
            show_error(self._window, message)
            return
        self._host.toast(_("Exported “{name}”").format(name=target.name))
