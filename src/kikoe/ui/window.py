"""The main window: notes sidebar on the left, the selected note on the right."""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from gi.repository import Adw, Gio, GLib, GObject, Gtk, Pango

from kikoe.core.choice import ModelChoice
from kikoe.core.export import ExportFormat, export_filename, export_note
from kikoe.core.formatting import transcript_features
from kikoe.core.models import DisplayOptions, Note
from kikoe.core.validation import Capability
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionOptions
from kikoe.providers.factory import create_client
from kikoe.services.importer import ImportStage, import_audio
from kikoe.storage.notes import NoteRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.ui.bottom_bar import BottomBar
from kikoe.ui.items import NoteItem
from kikoe.ui.labels import (
    export_format_label,
    import_stage_label,
    note_subtitle,
    note_title_subtitle,
)
from kikoe.ui.note_view import NoteView
from kikoe.ui.tasks import USER_FACING_ERRORS, run_task, show_error
from kikoe.ui.templates import ui_file

log = logging.getLogger(__name__)

_NOTE_ACTIONS = (
    "rename-note",
    "copy-all-text",
    "delete-note",
    "export-txt",
    "export-md",
    "export-srt",
    "export-vtt",
)
"""Window actions that act on the shown note and are disabled without one."""


@Gtk.Template(filename=ui_file("window"))
class Window(Adw.ApplicationWindow):
    __gtype_name__ = "KikoeWindow"

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    split_view: Adw.NavigationSplitView = Gtk.Template.Child()
    notes_list: Gtk.ListBox = Gtk.Template.Child()
    content_page: Adw.NavigationPage = Gtk.Template.Child()
    window_title: Adw.WindowTitle = Gtk.Template.Child()
    note_menu_button: Gtk.MenuButton = Gtk.Template.Child()
    export_button: Gtk.MenuButton = Gtk.Template.Child()
    export_popover: Gtk.Popover = Gtk.Template.Child()
    export_srt_button: Gtk.Button = Gtk.Template.Child()
    export_vtt_button: Gtk.Button = Gtk.Template.Child()
    display_options_button: Gtk.MenuButton = Gtk.Template.Child()
    timestamps_row: Adw.SwitchRow = Gtk.Template.Child()
    speakers_row: Adw.SwitchRow = Gtk.Template.Child()
    content_stack: Gtk.Stack = Gtk.Template.Child()
    note_view_slot: Adw.Bin = Gtk.Template.Child()
    bottom_bar_slot: Adw.Bin = Gtk.Template.Child()

    def __init__(
        self, application: Gtk.Application, providers: ProviderRepository, notes: NoteRepository
    ) -> None:
        super().__init__(application=application)
        # Custom widgets are added from code: Blueprint's linter cannot inspect their types.
        self.note_view = NoteView()
        self.note_view_slot.set_child(self.note_view)
        self.bottom_bar = BottomBar()
        self.bottom_bar_slot.set_child(self.bottom_bar)
        self._providers = providers
        self._notes = notes
        self._note: Note | None = None
        self._providers_loaded = False
        self._selecting = False
        """True while the sidebar selection is changed from code, not by the user."""
        self._showing = False
        """True while widgets are filled from a note, so their signals are not user input."""
        self._note_items: Gio.ListStore[NoteItem] = Gio.ListStore(item_type=NoteItem)
        self.notes_list.bind_model(self._note_items, self._create_note_row)
        self._add_action("new-note", self._on_new_note)
        self._add_action("rename-note", self._on_rename_note)
        self._add_action("copy-all-text", self._on_copy_all_text)
        self._add_action("delete-note", self._on_delete_note)
        self._add_action("export-txt", self._on_export_txt)
        self._add_action("export-md", self._on_export_md)
        self._add_action("export-srt", self._on_export_srt)
        self._add_action("export-vtt", self._on_export_vtt)
        self._add_action("import-audio", self._on_import_audio)
        self._add_action("cancel-import", self._on_cancel_import)
        self._import_task: asyncio.Task[None] | None = None
        self._set_action_enabled("new-note", False)  # Until providers are loaded.
        self._show_note(None)
        providers.connect_changed(self._on_providers_changed)
        notes.connect_changed(self._on_notes_changed)
        self.bottom_bar.connect("choice-changed", self._on_bottom_bar_choice_changed)
        self.bottom_bar.bind_providers(providers)

    @property
    def note(self) -> Note | None:
        """The note shown in the content pane."""
        return self._note

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_overlay.add_toast(toast)

    def select_note(self, note_id: str) -> None:
        """Select ``note_id`` in the sidebar and show it."""
        if self._select_row(note_id):
            run_task(self._open_note(note_id), self)
            self.split_view.set_show_content(True)

    def _add_action(
        self, name: str, callback: Callable[[Gio.SimpleAction, GLib.Variant | None], None]
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)

    # Sidebar

    def _create_note_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, NoteItem)
        summary = item.summary
        title = Gtk.Label(label=summary.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        subtitle = Gtk.Label(
            label=note_subtitle(summary, datetime.now().astimezone()),
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            css_classes=["caption", "dim-label"],
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6
        )
        box.append(title)
        box.append(subtitle)
        return box

    def _select_row(self, note_id: str | None) -> bool:
        """Select the row of ``note_id`` without reopening it; False if it is not listed."""
        index = next(
            (i for i, item in enumerate(self._note_items) if item.summary.note_id == note_id),
            None,
        )
        row = self.notes_list.get_row_at_index(index) if index is not None else None
        self._selecting = True
        try:
            if row is None:
                self.notes_list.unselect_all()
            else:
                self.notes_list.select_row(row)
        finally:
            self._selecting = False
        return row is not None

    @Gtk.Template.Callback()
    def _on_notes_list_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._selecting or row is None:
            return
        item = self._note_items.get_item(row.get_index())
        if item is not None:
            run_task(self._open_note(item.summary.note_id), self)
            self.split_view.set_show_content(True)

    def _on_notes_changed(self, note_id: str | None) -> None:
        self._note_items.splice(
            0, self._note_items.get_n_items(), [NoteItem(s) for s in self._notes.notes]
        )
        current = self._note.note_id if self._note else None
        if current is None:
            self._update_content()
            return
        if not self._select_row(current):  # The shown note was deleted.
            self._show_note(None)
            self.split_view.set_show_content(False)
        elif note_id == current:
            run_task(self._open_note(current), self)

    def _on_providers_changed(self) -> None:
        self._providers_loaded = True
        # Matches the first-run design: nothing to transcribe with, so no new notes yet.
        self._set_action_enabled("new-note", bool(self._providers.providers))
        self._update_content()

    def _set_action_enabled(self, name: str, enabled: bool) -> None:
        action = self.lookup_action(name)
        if isinstance(action, Gio.SimpleAction):
            action.set_enabled(enabled)

    # Content

    async def _open_note(self, note_id: str) -> None:
        self._show_note(await self._notes.get_note(note_id))

    def _show_note(self, note: Note | None) -> None:
        self._note = note
        self.note_menu_button.set_visible(note is not None)
        self.export_button.set_visible(note is not None)
        self.display_options_button.set_visible(note is not None)
        for name in _NOTE_ACTIONS:
            self._set_action_enabled(name, note is not None)
        if note is None:
            self.content_page.set_title(_("Kikoe"))
            self.window_title.set_title("")
            self.window_title.set_subtitle("")
        else:
            self.content_page.set_title(note.title)
            self.window_title.set_title(note.title)
            self.window_title.set_subtitle(note_title_subtitle(note))
            self._showing = True
            try:
                self.timestamps_row.set_active(note.display.show_timestamps)
                self.speakers_row.set_active(note.display.show_speakers)
            finally:
                self._showing = False
            self.note_view.show_note(note)
            self._update_subtitle_buttons(note)
        self._update_content()

    @Gtk.Template.Callback()
    def _on_display_row_notify_active(
        self, _row: Adw.SwitchRow, _pspec: GObject.ParamSpec | None
    ) -> None:
        if self._showing or self._note is None:
            return
        options = DisplayOptions(self.timestamps_row.get_active(), self.speakers_row.get_active())
        if options != self._note.display:
            run_task(self._notes.set_display_options(self._note.note_id, options), self)

    def _update_content(self) -> None:
        if self._note is not None:
            page = "note"
        elif not self._providers_loaded:
            page = "loading"
        elif not self._providers.providers:
            page = "welcome"
        else:
            page = "empty"
        self.content_stack.set_visible_child_name(page)
        self.bottom_bar_slot.set_visible(page in ("note", "empty"))

    # Note actions

    def _on_new_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        run_task(self._create_note(), self)

    async def _create_note(self) -> None:
        note = await self._notes.create_note(_("New Note"))
        self.select_note(note.note_id)

    def _on_rename_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        note = self._note
        if note is None:
            return
        entry = Gtk.Entry(text=note.title, activates_default=True)
        dialog = Adw.AlertDialog(heading=_("Rename Note"), extra_child=entry)
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("rename", _("_Rename"))
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        def update_enabled(_entry: Gtk.Entry) -> None:
            dialog.set_response_enabled("rename", bool(entry.get_text().strip()))

        def respond(_dialog: Adw.AlertDialog, response: str) -> None:
            title = entry.get_text().strip()
            if response == "rename" and title and title != note.title:
                run_task(self._notes.rename_note(note.note_id, title), self)

        entry.connect("changed", update_enabled)
        dialog.connect("response", respond)
        dialog.present(self)
        entry.grab_focus()

    def _on_delete_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        note = self._note
        if note is None:
            return
        dialog = Adw.AlertDialog(
            heading=_("Delete “{title}”?").format(title=note.title),
            body=_("Its transcript and original audio will be deleted. This cannot be undone."),
        )
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("delete", _("_Delete"))
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def respond(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "delete":
                run_task(self._notes.delete_note(note.note_id), self)

        dialog.connect("response", respond)
        dialog.present(self)

    # Export

    def _update_subtitle_buttons(self, note: Note) -> None:
        """Subtitle buttons need timestamps; explain when they are unavailable."""
        has_times = any(
            Capability.TIMESTAMPS in transcript_features(r.segments) for r in note.recordings
        )
        for button, fmt in (
            (self.export_srt_button, ExportFormat.SRT),
            (self.export_vtt_button, ExportFormat.WEBVTT),
        ):
            button.set_sensitive(has_times)
            if has_times:
                label = export_format_label(fmt)
                button.set_tooltip_text(_("Export this note as {format}.").format(format=label))
            else:
                button.set_tooltip_text(
                    _("This note has no timestamps, so subtitles are unavailable.")
                )

    def _on_copy_all_text(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        note = self._note
        if note is None:
            return
        self.get_clipboard().set(export_note(note, ExportFormat.TEXT, note.display))
        self.add_toast(Adw.Toast(title=_("Copied to clipboard")))

    def _on_export_txt(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._begin_export(ExportFormat.TEXT)

    def _on_export_md(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._begin_export(ExportFormat.MARKDOWN)

    def _on_export_srt(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._begin_export(ExportFormat.SRT)

    def _on_export_vtt(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._begin_export(ExportFormat.WEBVTT)

    def _begin_export(self, fmt: ExportFormat) -> None:
        self.export_popover.popdown()
        run_task(self._export_note(fmt), self)

    async def _export_note(self, fmt: ExportFormat) -> None:
        note = self._note
        if note is None:
            return
        content = export_note(note, fmt, note.display)
        target = await self._pick_save_path(
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
            show_error(self, message)
            return
        self.add_toast(Adw.Toast(title=_("Exported “{name}”").format(name=target.name)))

    async def _pick_save_path(
        self, title: str, initial_name: str, filter_label: str, pattern: str
    ) -> Path | None:
        """Ask where to save; None if the dialog was dismissed."""
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
        future: asyncio.Future[Gio.File | None] = asyncio.get_running_loop().create_future()

        def done(_dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                future.set_result(dialog.save_finish(result))
            except GLib.Error as error:
                if error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    future.set_result(None)
                else:
                    future.set_exception(error)

        dialog.save(self, None, done)
        file = await future
        if file is None:
            return None
        path = file.get_path()
        if path is None:
            show_error(self, _("Only locations on this computer can be used."))
            return None
        return Path(path)

    # Import

    def _on_bottom_bar_choice_changed(self, _bar: BottomBar) -> None:
        self._update_import_actions()

    def _update_import_actions(self) -> None:
        busy = self._import_task is not None
        self._set_action_enabled("import-audio", not busy and self.bottom_bar.choice is not None)
        self._set_action_enabled("cancel-import", busy)

    def _on_import_audio(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        run_task(self._pick_and_import(), self)

    def _on_cancel_import(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._import_task is not None:
            self._import_task.cancel()

    async def _pick_and_import(self) -> None:
        path = await self._pick_audio_file()
        choice = self.bottom_bar.choice
        if path is None or choice is None or self._import_task is not None:
            return
        note_id = self._note.note_id if self._note else None
        features = self.bottom_bar.requested_features
        options = TranscriptionOptions(
            timestamps=features.timestamps, diarization=features.diarization
        )
        task = asyncio.ensure_future(self._import(path, note_id, choice, options))
        self._import_task = task
        self._update_import_actions()
        self.bottom_bar.set_progress(import_stage_label(ImportStage.CONVERTING, 1, 1))
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise  # This coroutine itself was cancelled, not the import.
            self.add_toast(Adw.Toast(title=_("Import cancelled")))
        finally:
            self._import_task = None
            self.bottom_bar.set_progress(None)
            self._update_import_actions()

    async def _pick_audio_file(self) -> Path | None:
        """Ask for an audio or video file; None if the dialog was dismissed."""
        media = Gtk.FileFilter(name=_("Audio and Video"))
        media.add_mime_type("audio/*")
        media.add_mime_type("video/*")
        filters: Gio.ListStore[Gtk.FileFilter] = Gio.ListStore(item_type=Gtk.FileFilter)
        filters.append(media)
        dialog = Gtk.FileDialog(title=_("Import Audio"), filters=filters, default_filter=media)
        future: asyncio.Future[Gio.File | None] = asyncio.get_running_loop().create_future()

        def done(_dialog: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                future.set_result(dialog.open_finish(result))
            except GLib.Error as error:
                if error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                    future.set_result(None)
                else:
                    future.set_exception(error)

        dialog.open(self, None, done)
        file = await future
        if file is None:
            return None
        path = file.get_path()
        if path is None:
            show_error(self, _("Only files on this computer can be imported."))
            return None
        return Path(path)

    async def _import(
        self,
        path: Path,
        note_id: str | None,
        choice: ModelChoice,
        options: TranscriptionOptions,
    ) -> None:
        def on_progress(stage: ImportStage, part: int, parts: int) -> None:
            self.bottom_bar.set_progress(import_stage_label(stage, part, parts))

        try:
            api_key = await self._providers.get_api_key(choice.provider.provider_id)
            note = await import_audio(
                path,
                note_id,
                choice.provider,
                choice.model,
                client=create_client(choice.provider, api_key),
                notes=self._notes,
                options=options,
                on_progress=on_progress,
            )
        except USER_FACING_ERRORS as error:
            log.warning("Import of %s failed: %s", path, error)
            self._show_import_failure(str(error))
            return
        self.select_note(note.note_id)

    def _show_import_failure(self, message: str) -> None:
        toast = Adw.Toast(title=_("Transcription failed"), button_label=_("_Details"), timeout=10)

        def show_details(_toast: Adw.Toast) -> None:
            show_error(self, message)

        toast.connect("button-clicked", show_details)
        self.add_toast(toast)
