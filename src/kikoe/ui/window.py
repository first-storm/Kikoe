"""The main window: notes sidebar on the left, the selected note on the right."""

import asyncio
import logging
import shutil
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from kikoe.core.choice import ModelChoice, RequestedFeatures, available_models
from kikoe.core.edit_history import SegmentFields
from kikoe.core.export import ExportFormat, export_filename, export_note
from kikoe.core.models import (
    DisplayOptions,
    Note,
    NoteSummary,
    Recording,
    RecordingSource,
    TranscriptionState,
)
from kikoe.core.preferences import WindowState, valid_window_state
from kikoe.core.recording import CaptureProgress, CaptureState
from kikoe.core.speakers import identity_for
from kikoe.core.transcription import (
    FileTranscriptSnapshot,
    LiveSnapshot,
)
from kikoe.i18n import _
from kikoe.media.ffmpeg import find_ffmpeg
from kikoe.media.player import PlaybackError
from kikoe.media.recorder import SoundDeviceRecorder
from kikoe.providers.base import TranscriptionOptions
from kikoe.providers.factory import create_client
from kikoe.services.editing import EditingSession
from kikoe.services.importer import ImportStage, import_audio
from kikoe.services.playback import Playback
from kikoe.services.realtime import RealtimeRecording
from kikoe.services.recording import RecordingSession, recover_recording, transcribe_recording
from kikoe.storage.captures import CaptureStore, capture_dir
from kikoe.storage.notes import NoteRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.storage.settings import SettingsError
from kikoe.ui.bottom_bar import BottomBar
from kikoe.ui.dialogs import confirm_destructive
from kikoe.ui.export_dialog import ExportDialog
from kikoe.ui.file_transcription import FileTranscriptionDisplay
from kikoe.ui.items import NoteItem
from kikoe.ui.labels import (
    export_format_label,
    import_stage_label,
    note_subtitle,
    note_title_subtitle,
    recording_note_title,
)
from kikoe.ui.note_view import NoteView
from kikoe.ui.retranscribe_dialog import RetranscribeDialog
from kikoe.ui.search import NoteSearch
from kikoe.ui.speaker_dialog import SpeakerDialog
from kikoe.ui.speaker_editor import SpeakerEditor
from kikoe.ui.tasks import USER_FACING_ERRORS, run_task, show_error, show_notice
from kikoe.ui.templates import ui_file
from kikoe.ui.transcript_row import NO_TIME

log = logging.getLogger(__name__)

_NOTE_ACTIONS = (
    "rename-note",
    "copy-all-text",
    "delete-note",
    "export",
    "show-timestamps",
    "show-speakers",
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
    display_options_button: Gtk.MenuButton = Gtk.Template.Child()
    content_stack: Gtk.Stack = Gtk.Template.Child()
    note_view_slot: Adw.Bin = Gtk.Template.Child()
    bottom_bar_slot: Adw.Bin = Gtk.Template.Child()

    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_button: Gtk.ToggleButton = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    notes_placeholder: Adw.StatusPage = Gtk.Template.Child()

    def __init__(
        self,
        application: Gtk.Application,
        providers: ProviderRepository,
        notes: NoteRepository,
        window_state: WindowState | None = None,
    ) -> None:
        super().__init__(application=application)
        self._geometry_timeout = 0
        if window_state is not None:
            self.set_default_size(window_state.width, window_state.height)
            if window_state.maximized:
                self.maximize()
        for prop in ("default-width", "default-height", "maximized"):
            self.connect(f"notify::{prop}", self._on_geometry_changed)
        # Custom widgets are added from code: Blueprint's linter cannot inspect their types.
        self.note_view = NoteView()
        self.note_view_slot.set_child(self.note_view)
        self.note_view.connect("text-edited", self._on_note_view_text_edited)
        self.note_view.connect("times-edited", self._on_note_view_times_edited)
        self.note_view.connect("segment-left", self._on_note_view_segment_left)
        self._editing = EditingSession(
            notes, lambda coro: run_task(coro, self), self._on_edit_saved
        )
        self._edits_blocked = False
        self.bottom_bar = BottomBar()
        self.bottom_bar_slot.set_child(self.bottom_bar)
        self._search = NoteSearch(
            notes,
            lambda coro: run_task(coro, self),
            self.search_entry.get_text,
            self._refresh_note_list,
        )
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self.notes_list)
        self._add_action("search-notes", self._on_search_notes)
        for action, keys in (
            ("new-note", ["<primary>n"]),
            ("search-notes", ["<primary>f"]),
            ("import-audio", ["<primary>o"]),
            ("rename-note", ["F2"]),
            ("export", ["<primary><shift>e"]),
            ("undo", ["<primary>z"]),
            ("redo", ["<primary><shift>z", "<primary>y"]),
        ):
            application.set_accels_for_action(f"win.{action}", keys)
        self._providers = providers
        self._notes = notes
        self._note: Note | None = None
        self._providers_loaded = False
        self._selecting = False
        """True while the sidebar selection is changed from code, not by the user."""
        self._note_items: Gio.ListStore[NoteItem] = Gio.ListStore(item_type=NoteItem)
        self.notes_list.bind_model(self._note_items, self._create_note_row)
        self._add_action("new-note", self._on_new_note)
        self._add_action("rename-note", self._on_rename_note)
        self._add_action("copy-all-text", self._on_copy_all_text)
        self._add_action("delete-note", self._on_delete_note)
        self._add_action("rename-note-row", self._on_rename_note_row, GLib.VariantType.new("s"))
        self._add_action("delete-note-row", self._on_delete_note_row, GLib.VariantType.new("s"))
        self._add_action("export", self._on_export)
        self._add_toggle_action("show-timestamps")
        self._add_toggle_action("show-speakers")
        self._add_action("export-audio", self._on_export_audio, GLib.VariantType.new("s"))
        self._add_action("choose-model", self._on_choose_model)
        self._add_action("import-audio", self._on_import_audio)
        self._add_action("cancel-import", self._on_cancel_import)
        self._import_task: asyncio.Task[None] | None = None
        self._committing_transcription = False
        self._file_display = FileTranscriptionDisplay(self.note_view)
        self._recording_task: asyncio.Task[None] | None = None
        self._live: RealtimeRecording | None = None
        self._session: RecordingSession | None = None
        self._recording_note_id: str | None = None
        self._recording_summary = ""
        self._closing = False
        self._recovery_checked = False
        self._recovery_busy = False
        self._recovery_task: asyncio.Task[None] | None = None
        self._add_action("start-recording", self._on_start_recording)
        self._add_action("pause-recording", self._on_pause_recording)
        self._add_action("resume-recording", self._on_resume_recording)
        self._add_action("stop-recording", self._on_stop_recording)
        self._add_action("retry-recording", self._on_retry_recording, GLib.VariantType.new("s"))
        self._playback = Playback(
            lambda coro: run_task(coro, self), self.note_view.set_playing, self._on_playback_error
        )
        self._add_action("play-recording", self._on_play_recording, GLib.VariantType.new("s"))
        self._add_action("play-sample", self._on_play_sample, GLib.VariantType.new("(sdd)"))
        self._add_action("edit-speaker", self._on_edit_speaker, GLib.VariantType.new("(ss)"))
        self._add_action("undo", self._on_undo)
        self._add_action("redo", self._on_redo)
        self._add_action("match-speakers", self._on_match_speakers, GLib.VariantType.new("s"))
        self._add_action(
            "retranscribe-recording", self._on_retranscribe_recording, GLib.VariantType.new("s")
        )
        self._add_action("delete-recording", self._on_delete_recording, GLib.VariantType.new("s"))
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

    def _on_geometry_changed(self, _window: Gtk.Window, _spec: GObject.ParamSpec) -> None:
        """Save the size shortly after the user stops resizing, not on every frame."""
        if self._geometry_timeout:
            GLib.source_remove(self._geometry_timeout)
        self._geometry_timeout = GLib.timeout_add(1000, self._start_geometry_save)

    def _start_geometry_save(self) -> bool:
        self._geometry_timeout = 0
        run_task(self._save_geometry(), self)
        return GLib.SOURCE_REMOVE

    async def _save_geometry(self) -> None:
        width, height = self.get_default_size()
        state = WindowState(width, height, self.is_maximized())
        if not valid_window_state(state):
            return
        try:
            await self._providers.update_window(state)
        except SettingsError as error:
            log.warning("Could not remember the window size: %s", error)

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_overlay.add_toast(toast)

    def select_note(self, note_id: str) -> None:
        """Select ``note_id`` in the sidebar and show it."""
        self.search_bar.set_search_mode(False)
        self.search_entry.set_text("")
        self._refresh_note_list(self._notes.notes)
        if self._select_row(note_id):
            run_task(self._open_note(note_id), self)
            self.split_view.set_show_content(True)

    def _add_action(
        self,
        name: str,
        callback: Callable[[Gio.SimpleAction, GLib.Variant | None], None],
        parameter_type: GLib.VariantType | None = None,
    ) -> None:
        action = Gio.SimpleAction.new(name, parameter_type)
        action.connect("activate", callback)
        self.add_action(action)

    def _add_toggle_action(self, name: str) -> None:
        action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(False))
        action.connect("activate", self._on_display_action_activate)
        self.add_action(action)

    def _display_action_state(self, name: str) -> bool:
        action = self.lookup_action(name)
        return action is not None and action.get_state() == GLib.Variant.new_boolean(True)

    def _set_display_action_state(self, name: str, value: bool) -> None:
        action = self.lookup_action(name)
        if isinstance(action, Gio.SimpleAction):
            action.set_state(GLib.Variant.new_boolean(value))

    def _on_display_action_activate(
        self, action: Gio.SimpleAction, _param: GLib.Variant | None
    ) -> None:
        if self._note is None:
            return
        action.set_state(
            GLib.Variant.new_boolean(not self._display_action_state(action.get_name()))
        )
        options = DisplayOptions(
            self._display_action_state("show-timestamps"),
            self._display_action_state("show-speakers"),
        )
        if options != self._note.display:
            run_task(self._notes.set_display_options(self._note.note_id, options), self)

    # Sidebar

    def _on_search_notes(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self.split_view.set_show_content(False)
        self.search_bar.set_search_mode(True)
        self.search_entry.grab_focus()

    @Gtk.Template.Callback()
    def _on_search_entry_stop_search(self, _entry: Gtk.SearchEntry) -> None:
        self.search_bar.set_search_mode(False)

    @Gtk.Template.Callback()
    def _on_search_bar_notify_search_mode_enabled(
        self, _bar: Gtk.SearchBar, _spec: GObject.ParamSpec
    ) -> None:
        if not self.search_bar.get_search_mode():
            self.search_entry.set_text("")
            self._start_search()

    @Gtk.Template.Callback()
    def _on_search_entry_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._start_search()

    def _start_search(self) -> None:
        self._search.refresh()

    def _refresh_note_list(self, notes: tuple[NoteSummary, ...]) -> None:
        self._selecting = True
        try:
            self._note_items.splice(0, self._note_items.get_n_items(), [NoteItem(s) for s in notes])
            searching = bool(self.search_entry.get_text().strip())
            self.notes_placeholder.set_title(
                _("No Matching Notes") if searching else _("No Notes Yet")
            )
            self.notes_placeholder.set_icon_name(
                "edit-find-symbolic" if searching else "document-edit-symbolic"
            )
        finally:
            self._selecting = False
        self._select_row(self._note.note_id if self._note else None)

    def _create_note_row(self, item: GObject.Object) -> Gtk.Widget:
        assert isinstance(item, NoteItem)
        summary = item.summary
        title = Gtk.Label(label=summary.title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        session = self._session
        active = summary.note_id == self._recording_note_id and session is not None
        subtitle = Gtk.Label(
            label=_("Paused")
            if active and session is not None and session.state is CaptureState.PAUSED
            else _("Recording")
            if active and session is not None and session.state is CaptureState.RECORDING
            else note_subtitle(summary, datetime.now().astimezone()),
            xalign=0,
            ellipsize=Pango.EllipsizeMode.END,
            css_classes=["caption", "dimmed"],
        )
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6
        )
        box.append(title)
        if active and session is not None and session.state is not CaptureState.STOPPED:
            subtitle.remove_css_class("dimmed")
            subtitle.add_css_class("recording-status")
            indicator = Gtk.Image(icon_name="media-record-symbolic", pixel_size=7)
            indicator.add_css_class("recording-indicator")
            status = Gtk.Box(spacing=6)
            status.append(indicator)
            status.append(subtitle)
            box.append(status)
        else:
            box.append(subtitle)
        # Secondary click and long press offer Rename and Delete without opening the note.
        secondary = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        secondary.connect("pressed", self._on_note_row_menu_requested, box, summary.note_id)
        long_press = Gtk.GestureLongPress()
        long_press.connect("pressed", self._on_note_row_menu_requested_at, box, summary.note_id)
        box.add_controller(secondary)
        box.add_controller(long_press)
        return box

    def _on_note_row_menu_requested(
        self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float, *data: object
    ) -> None:
        box, note_id = data
        assert isinstance(box, Gtk.Widget) and isinstance(note_id, str)
        self._popup_note_row_menu(box, note_id, x, y)

    def _on_note_row_menu_requested_at(
        self, _gesture: Gtk.GestureLongPress, x: float, y: float, *data: object
    ) -> None:
        box, note_id = data
        assert isinstance(box, Gtk.Widget) and isinstance(note_id, str)
        self._popup_note_row_menu(box, note_id, x, y)

    def _popup_note_row_menu(self, box: Gtk.Widget, note_id: str, x: float, y: float) -> None:
        row = box.get_ancestor(Gtk.ListBoxRow)
        assert isinstance(row, Gtk.ListBoxRow)
        rename = Gio.MenuItem()
        rename.set_label(_("_Rename…"))
        rename.set_action_and_target_value("win.rename-note-row", GLib.Variant("s", note_id))
        delete = Gio.MenuItem()
        delete.set_label(_("_Delete Note"))
        delete.set_action_and_target_value("win.delete-note-row", GLib.Variant("s", note_id))
        menu = Gio.Menu()
        menu.append_item(rename)
        menu.append_item(delete)
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(box)
        popover.set_has_arrow(False)
        popover.set_halign(Gtk.Align.START)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = round(x), round(y), 1, 1
        popover.set_pointing_to(rect)

        def release(_popover: Gtk.Popover) -> None:
            row.remove_css_class("has-open-popup")
            GLib.idle_add(popover.unparent)

        popover.connect("closed", release)
        # Keep the menu's target highlighted independently of pointer hover.
        row.add_css_class("has-open-popup")
        popover.popup()

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
        if self._switch_locked():
            self._select_row(self._note.note_id if self._note else None)
            return
        item = self._note_items.get_item(row.get_index())
        if item is not None:
            run_task(self._open_note(item.summary.note_id), self)
            self.split_view.set_show_content(True)

    def _switch_locked(self) -> bool:
        """Whether the open note must stay put; transcription alone never pins it."""
        return self._recovery_busy or (
            self._recording_task is not None and self._import_task is None
        )

    def _viewing_transcription(self) -> bool:
        """Whether the note on screen is the one a running transcription writes to."""
        if self._import_task is None:
            return False
        return self._note is None or self._note.note_id == self._file_display.note_id

    def _on_notes_changed(self, note_id: str | None) -> None:
        if not self._recovery_checked:
            self._recovery_checked = True
            self._recovery_task = run_task(self._offer_recovery(), self)
        self._start_search()
        current = self._note.note_id if self._note else None
        if current is None:
            self._update_content()
            return
        if not any(s.note_id == current for s in self._notes.notes):
            self._show_note(None)
            self.split_view.set_show_content(False)
        elif note_id == current:
            run_task(self._open_note(current), self)

    def _on_providers_changed(self) -> None:
        self._providers_loaded = True
        # Matches the first-run design: nothing to transcribe with, so no new notes yet.
        self._set_action_enabled("new-note", bool(self._providers.providers))
        self._update_content()
        self._update_import_actions()

    def _set_action_enabled(self, name: str, enabled: bool) -> None:
        action = self.lookup_action(name)
        if isinstance(action, Gio.SimpleAction):
            action.set_enabled(enabled)

    # Content

    async def _open_note(self, note_id: str) -> None:
        note = await self._notes.get_note(note_id)
        current = self._recording_note_id or (self._note.note_id if self._note else None)
        if not self._switch_locked() or note_id == current:
            self._show_note(note)

    def _show_note(self, note: Note | None) -> None:
        self._file_display.hide_if_other(note)
        if self._note is not None and (note is None or self._note.note_id != note.note_id):
            self._playback.stop()
            # Saved under the note being left, so this must run before it is replaced.
            self.note_view.flush_edits()
            self._forget_edits()
        self._note = note
        self._editing.set_note(note)
        self.note_menu_button.set_visible(note is not None)
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
            self._set_display_action_state("show-timestamps", note.display.show_timestamps)
            self._set_display_action_state("show-speakers", note.display.show_speakers)
            if self.note_view.show_note(note):
                self._forget_edits()
            self._file_display.show(note)
            self.bottom_bar.set_note(bool(note.recordings))
            if self._session is not None and self._session.state is not CaptureState.STOPPED:
                self.note_view.show_recording(
                    self._recording_summary, self._recording_model, realtime=self._live is not None
                )
                if self._live is not None:
                    self.note_view.update_live(self._live.snapshot())
                self.note_view.recording_view.set_state(self._session.state)
                self.window_title.set_subtitle(
                    _("Paused") if self._session.state is CaptureState.PAUSED else _("Recording")
                )
        self._update_content()
        self._update_import_actions()

    def _update_content(self) -> None:
        if self._note is not None or self._file_display.active:
            page = "note"
        elif not self._providers_loaded:
            page = "loading"
        elif not self._providers.providers:
            page = "welcome"
        else:
            page = "empty"
        self.content_stack.set_visible_child_name(page)
        self.bottom_bar_slot.set_visible(page == "note")

    # Note actions

    def _on_new_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        run_task(self._create_note(), self)

    async def _create_note(self) -> None:
        note = await self._notes.create_note(_("New Note"))
        self.select_note(note.note_id)

    def _on_rename_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._note is not None:
            self._rename_note(self._note.note_id, self._note.title)

    def _on_rename_note_row(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        summary = self._summary(param.get_string()) if param is not None else None
        if summary is not None:
            self._rename_note(summary.note_id, summary.title)

    def _summary(self, note_id: str) -> NoteSummary | None:
        return next((s for s in self._notes.notes if s.note_id == note_id), None)

    def _rename_note(self, note_id: str, current_title: str) -> None:
        entry = Gtk.Entry(text=current_title, activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], [_("Note Title")])
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
            if response == "rename" and title and title != current_title:
                run_task(self._notes.rename_note(note_id, title), self)

        entry.connect("changed", update_enabled)
        dialog.connect("response", respond)
        dialog.present(self)
        entry.grab_focus()

    def _on_delete_note(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._note is not None:
            self._delete_note(self._note.note_id, self._note.title)

    def _on_delete_note_row(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        summary = self._summary(param.get_string()) if param is not None else None
        if summary is not None:
            self._delete_note(summary.note_id, summary.title)

    def _delete_note(self, note_id: str, title: str) -> None:
        confirm_destructive(
            self,
            _("Delete “{title}”?").format(title=title),
            _("Its transcript and original audio will be deleted. This cannot be undone."),
            _("_Delete"),
            lambda: run_task(self._notes.delete_note(note_id), self),
        )

    def _on_delete_recording(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        note = self._note
        if note is None or param is None:
            return
        recording_id = param.get_string()
        recording = self._recording_by_id(recording_id)
        if recording is None:
            self.add_toast(Adw.Toast(title=_("This recording is no longer available.")))
            return
        number = note.recordings.index(recording) + 1
        self._playback.stop()
        confirm_destructive(
            self,
            _("Delete Recording {number}?").format(number=number),
            _("Its transcript and original audio will be deleted. This cannot be undone."),
            _("_Delete"),
            lambda: run_task(self._delete_recording(note.note_id, recording_id), self),
        )

    async def _delete_recording(self, note_id: str, recording_id: str) -> None:
        note = await self._notes.delete_recording(note_id, recording_id)
        self._show_note(note)

    # Speaker identities and playback

    def _recording_by_id(self, recording_id: str) -> Recording | None:
        return self._note.recording(recording_id) if self._note else None

    def _on_play_recording(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is not None:
            self._request_playback(param.get_string(), 0, None)

    def _on_play_sample(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is not None:
            recording_id, start, end = param.unpack()
            self._request_playback(str(recording_id), float(start), float(end))

    def _request_playback(self, recording_id: str, start: float, end: float | None) -> None:
        recording = self._recording_by_id(recording_id)
        if recording is None or recording.audio_path is None:
            self.add_toast(Adw.Toast(title=_("The original audio is not available.")))
            return
        self._playback.play(
            recording.audio_path,
            recording_id,
            start,
            end,
            self._providers.preferences.output_device,
        )

    def _on_playback_error(self, error: PlaybackError) -> None:
        log.warning("Playback failed: %s", error)
        self.add_toast(Adw.Toast(title=str(error)))

    def _on_edit_speaker(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if self._note is None or param is None:
            return
        recording_id, source_id = param.unpack()
        recording = self._recording_by_id(str(recording_id))
        if recording is None:
            return
        person = identity_for(recording, str(source_id))
        if not any(s.speaker_id == person for s in self._note.speakers):
            return
        dialog = SpeakerEditor(
            self._notes, self._note, person, lambda: self._present_matching(str(recording_id))
        )
        dialog.connect("closed", self._on_audio_dialog_closed)
        dialog.present(self)

    def _on_match_speakers(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        if param is not None:
            self._present_matching(param.get_string())

    def _present_matching(self, recording_id: str) -> None:
        recording = self._recording_by_id(recording_id)
        if self._note is None or recording is None or not recording.speaker_bindings:
            self.add_toast(Adw.Toast(title=_("This recording has no detected speakers.")))
            return
        dialog = SpeakerDialog(self._notes, self._note, recording)
        dialog.connect("closed", self._on_audio_dialog_closed)
        dialog.present(self)

    def _on_audio_dialog_closed(self, _dialog: Adw.Dialog) -> None:
        self._playback.stop()

    def _on_retranscribe_recording(
        self, _action: Gio.SimpleAction, param: GLib.Variant | None
    ) -> None:
        if self._note is None or param is None:
            return
        recording = self._recording_by_id(param.get_string())
        if recording is None or recording.audio_path is None:
            self.add_toast(Adw.Toast(title=_("The original audio is not available.")))
            return
        note_id = self._note.note_id

        def submit(choice: ModelChoice, options: TranscriptionOptions) -> None:
            choices = available_models(self._providers.providers)
            refreshed = next((c for c in choices if c.key == choice.key), None)
            if refreshed is None or refreshed.model.capabilities.file is None:
                self.add_toast(Adw.Toast(title=_("Choose an available file transcription model.")))
                return
            self._playback.stop()
            self._import_task = run_task(
                self._retranscribe(note_id, recording, refreshed, options), self
            )
            self.bottom_bar.set_progress(_("Transcribing…"))
            self._update_import_actions()

        dialog = RetranscribeDialog(
            self._providers,
            recording,
            self._note.recordings.index(recording) + 1,
            self.bottom_bar.choice,
            submit,
        )
        dialog.connect("closed", self._on_audio_dialog_closed)
        dialog.present(self)

    async def _retranscribe(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        self._begin_file_transcription(recording, choice, replacing=True)
        try:
            await self._playback.finish()
            key = await self._providers.get_api_key(choice.provider.provider_id)
            note = await transcribe_recording(
                note_id,
                recording,
                choice,
                client=create_client(choice.provider, key),
                notes=self._notes,
                options=options,
                on_progress=self._on_file_progress,
                on_update=self._on_file_update,
                preserve_on_failure=True,
            )
            self._end_file_transcription()
            self._show_note(note)
        except asyncio.CancelledError:
            self.add_toast(Adw.Toast(title=_("Re-transcription cancelled")))
        except USER_FACING_ERRORS as error:
            log.warning("Re-transcription failed: %s", error)
            show_error(
                self,
                _("{error}\n\nYour text and audio have been kept.").format(error=error),
                _("Re-transcription Failed"),
            )
        finally:
            self._end_file_transcription()
            self._committing_transcription = False
            self._import_task = None
            self.bottom_bar.set_progress(None)
            self._update_import_actions()

    # In-place editing

    def _on_note_view_text_edited(
        self, _view: NoteView, recording_id: str, position: int, text: str
    ) -> None:
        self._edit_segment(
            recording_id, position, lambda fields: replace(fields, text=text.strip())
        )

    def _on_note_view_times_edited(
        self, _view: NoteView, recording_id: str, position: int, start_ms: int, end_ms: int
    ) -> None:
        self._edit_segment(
            recording_id,
            position,
            lambda fields: replace(fields, start=_from_ms(start_ms), end=_from_ms(end_ms)),
        )

    def _on_note_view_segment_left(self, _view: NoteView) -> None:
        self._editing.history.seal()

    def _edit_segment(
        self,
        recording_id: str,
        position: int,
        change: Callable[[SegmentFields], SegmentFields],
    ) -> None:
        if self._edits_blocked:
            return
        self._editing.edit(recording_id, position, change)
        self._update_history_actions()

    def _on_edit_saved(self, note: Note) -> None:
        if self._note is not None and self._note.note_id == note.note_id:
            self._show_note(note)

    def _forget_edits(self) -> None:
        self._editing.reset()
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        enabled = not self._edits_blocked and self._note is not None
        self._set_action_enabled("undo", enabled and self._editing.history.can_undo)
        self._set_action_enabled("redo", enabled and self._editing.history.can_redo)

    def _on_undo(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self.note_view.flush_edits()
        change = self._editing.undo()
        if change is not None:
            self.note_view.focus_segment(change.recording_id, change.position)
        self._update_history_actions()

    def _on_redo(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self.note_view.flush_edits()
        change = self._editing.redo()
        if change is not None:
            self.note_view.focus_segment(change.recording_id, change.position)
        self._update_history_actions()

    # Export

    def _on_export(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self.note_view.flush_edits()
        run_task(self._present_export(), self)

    async def _present_export(self) -> None:
        await self._editing.wait_saved()
        note = self._note
        if note is None:
            return
        ExportDialog(note, self._on_export_dialog_submitted).present(self)

    def _on_export_dialog_submitted(self, fmt: ExportFormat, options: DisplayOptions) -> None:
        run_task(self._export_note(fmt, options), self)

    def _on_copy_all_text(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self.note_view.flush_edits()
        run_task(self._copy_all_text(), self)

    async def _copy_all_text(self) -> None:
        await self._editing.wait_saved()
        note = self._note
        if note is None:
            return
        self.get_clipboard().set(export_note(note, ExportFormat.TEXT, note.display))
        self.add_toast(Adw.Toast(title=_("Copied to clipboard")))

    async def _export_note(self, fmt: ExportFormat, options: DisplayOptions) -> None:
        note = self._note
        if note is None:
            return
        content = export_note(note, fmt, options)
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

    def _on_export_audio(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        note = self._note
        if note is None or param is None:
            return
        recording = note.recording(param.get_string())
        if recording is None:
            self.add_toast(Adw.Toast(title=_("This recording is no longer available.")))
            return
        run_task(self._export_audio(note.recordings.index(recording) + 1, recording), self)

    async def _export_audio(self, number: int, recording: Recording) -> None:
        note = self._note
        source = recording.audio_path
        if note is None or source is None:
            return
        suffix = source.suffix or ".ogg"
        target = await self._pick_save_path(
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
            show_notice(self, _("The original audio is not available."))
            return
        except OSError as error:
            log.warning("Audio export of recording %s failed: %s", recording.recording_id, error)
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
        return await self._run_file_dialog(
            dialog.save, dialog.save_finish, _("Only locations on this computer can be used.")
        )

    async def _run_file_dialog(
        self,
        start: Callable[
            [Gtk.Window, None, Callable[[Gtk.FileDialog, Gio.AsyncResult], None]], None
        ],
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

        start(self, None, done)
        file = await future
        if file is None:
            return None
        path = file.get_path()
        if path is None:
            show_notice(self, not_local)
            return None
        return Path(path)

    # Import

    def _on_bottom_bar_choice_changed(self, _bar: BottomBar) -> None:
        self._update_import_actions()

    def _update_import_actions(self) -> None:
        busy = (
            self._import_task is not None or self._recording_task is not None or self._recovery_busy
        )
        locked = self._switch_locked() or self._viewing_transcription()
        if locked:
            self._playback.stop()
        for name in (
            "play-recording",
            "play-sample",
            "edit-speaker",
            "match-speakers",
            "retranscribe-recording",
            "delete-recording",
        ):
            self._set_action_enabled(name, not locked and self._note is not None)
        available = self.bottom_bar.choice is not None and not busy
        choice = self.bottom_bar.choice
        file_available = (
            available and choice is not None and choice.model.capabilities.file is not None
        )
        self._set_action_enabled("import-audio", file_available)
        self._set_action_enabled("start-recording", available)
        self._set_action_enabled("retry-recording", file_available and self._note is not None)
        self._set_action_enabled(
            "cancel-import", self._import_task is not None and not self._committing_transcription
        )
        session = self._session
        state = session.state if session is not None else CaptureState.STOPPED
        self._set_action_enabled(
            "pause-recording", state is CaptureState.RECORDING and not self._closing
        )
        self._set_action_enabled(
            "resume-recording", state is CaptureState.PAUSED and not self._closing
        )
        self._set_action_enabled(
            "stop-recording", state is not CaptureState.STOPPED and not self._closing
        )
        self.notes_list.set_can_target(not self._switch_locked())
        self._set_action_enabled("search-notes", not busy)
        self.search_button.set_sensitive(not busy)
        # Typing in the list starts a search, which must not hide the Stop controls.
        self.search_bar.set_key_capture_widget(None if busy else self.notes_list)
        self._set_action_enabled("new-note", not busy and bool(self._providers.providers))
        self._set_action_enabled("delete-note", not locked and self._note is not None)
        self._set_action_enabled("rename-note-row", not busy)
        self._set_action_enabled("delete-note-row", not busy)
        self._set_action_enabled("rename-note", not locked and self._note is not None)
        self._set_action_enabled("export", not locked and self._note is not None)
        self._set_action_enabled("export-audio", not locked and self._note is not None)
        self._edits_blocked = locked
        self.note_view.set_editable(not locked)
        self._update_history_actions()

    def _on_choose_model(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self.bottom_bar.present_transcription_dialog()

    def _on_import_audio(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        run_task(self._pick_and_import(), self)

    def _on_cancel_import(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._import_task is not None and not self._committing_transcription:
            self._import_task.cancel()

    def _transcription_options(self, features: RequestedFeatures) -> TranscriptionOptions:
        """Capture the current language with the entry point's selected features."""
        return TranscriptionOptions(
            timestamps=features.timestamps,
            diarization=features.diarization,
            language=self._providers.preferences.language,
            timestamp_granularities=features.timestamp_granularities,
        )

    async def _pick_and_import(self) -> None:
        path = await self._pick_audio_file()
        choice = self.bottom_bar.choice
        if (
            path is None
            or choice is None
            or self._import_task is not None
            or self._recording_task is not None
        ):
            return
        note_id = self._note.note_id if self._note else None
        options = self._transcription_options(self.bottom_bar.file_features)
        task = asyncio.ensure_future(self._import(path, note_id, choice, options))
        self._import_task = task
        self._update_import_actions()
        self.bottom_bar.set_progress(import_stage_label(ImportStage.CONVERTING, 1, 1))
        self.split_view.set_show_content(True)
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
        return await self._run_file_dialog(
            dialog.open, dialog.open_finish, _("Only files on this computer can be imported.")
        )

    async def _import(
        self,
        path: Path,
        note_id: str | None,
        choice: ModelChoice,
        options: TranscriptionOptions,
    ) -> None:
        recording = Recording(
            "file-preview",
            datetime.now(UTC),
            timedelta(),
            RecordingSource.IMPORTED,
            choice.provider.name,
            choice.model.model_id,
        )
        self._begin_file_transcription(recording, choice, title=path.stem)
        try:
            await self._playback.finish()
            api_key = await self._providers.get_api_key(choice.provider.provider_id)
            note = await import_audio(
                path,
                note_id,
                choice.provider,
                choice.model,
                client=create_client(choice.provider, api_key),
                notes=self._notes,
                options=options,
                on_progress=self._on_file_progress,
                on_update=self._on_file_update,
            )
        except USER_FACING_ERRORS as error:
            log.warning("Import of %s failed: %s", path, error)
            self._show_import_failure(str(error))
            return
        finally:
            self._end_file_transcription()
        if self._note is None or self._note.note_id == note.note_id:
            self.select_note(note.note_id)
        else:
            self.add_toast(Adw.Toast(title=_("Transcription finished")))

    def _begin_file_transcription(
        self,
        recording: Recording,
        choice: ModelChoice,
        *,
        replacing: bool = False,
        title: str = "",
    ) -> None:
        self._file_display.begin(
            self._note,
            replace(recording, provider_name=choice.provider.name, model_id=choice.model.model_id),
            replacing=replacing,
        )
        if self._note is None:
            self.content_page.set_title(title)
            self.window_title.set_title(title)
            self.window_title.set_subtitle(_("Transcribing…"))
        self._update_content()
        self.split_view.set_show_content(True)

    def _on_file_progress(self, stage: ImportStage, part: int, parts: int) -> None:
        self._committing_transcription = stage is ImportStage.SAVING
        self._set_action_enabled("cancel-import", not self._committing_transcription)
        self.bottom_bar.set_progress(import_stage_label(stage, part, parts))

    def _on_file_update(self, snapshot: FileTranscriptSnapshot) -> None:
        self._file_display.update(snapshot)

    def _end_file_transcription(self) -> None:
        if not self._file_display.active:
            return
        self._file_display.end()
        self._committing_transcription = False
        if self._note is None:
            self.content_page.set_title(_("Kikoe"))
            self.window_title.set_title("")
            self.window_title.set_subtitle("")
        self._update_content()

    def _show_import_failure(self, message: str) -> None:
        show_error(self, message, _("Transcription Failed"))

    # Recording

    def _on_start_recording(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        choice = self.bottom_bar.choice
        if choice is None or self._recording_task is not None or self._import_task is not None:
            return
        options = self._transcription_options(self.bottom_bar.requested_features)
        note_id = self._note.note_id if self._note else None
        self._recording_task = run_task(self._record(note_id, choice, options), self)
        self._update_import_actions()

    async def _record(
        self, note_id: str | None, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        live_task: asyncio.Task[None] | None = None
        realtime = choice.model.capabilities.realtime is not None
        try:
            await self._playback.finish()
            await asyncio.to_thread(find_ffmpeg)
            # Validate the batch backend before opening the microphone.
            client = create_client(
                choice.provider,
                await self._providers.get_api_key(choice.provider.provider_id) if realtime else "",
            )
            directory = await asyncio.to_thread(capture_dir)
            session = RecordingSession(
                SoundDeviceRecorder(self._providers.preferences.input_device),
                CaptureStore(directory),
                self._notes,
            )
            self._session = session
            title = recording_note_title(datetime.now(UTC))
            note = await session.start(note_id, title, choice)
            self._recording_note_id = note.note_id
            self._recording_model = choice.model.display_name
            labels: list[str] = []
            if options.timestamps:
                labels.append(_("Timestamps"))
            if options.diarization:
                labels.append(_("Speakers"))
            self._recording_summary = _("{provider} {model} · {options}").format(
                provider=choice.provider.name,
                model=choice.model.display_name,
                options=_("Text only") if not labels else ", ".join(labels),
            )
            if realtime:
                self._live = RealtimeRecording(
                    session, choice, client, options, self._on_live_update
                )
            self._show_note(note)
            self._refresh_recording_sidebar()
            self.split_view.set_show_content(True)
            self.bottom_bar.show_recording(self._recording_summary)
            self._on_capture_progress(CaptureProgress(CaptureState.RECORDING, session.elapsed, 0))
            if self._closing:
                await session.stop()
            if self._live is not None:
                live_task = run_task(self._live.run(), self)
            error = await session.capture(self._on_capture_progress)
            if live_task is not None:
                if self._closing:
                    live_task.cancel()
                else:
                    try:
                        async with asyncio.timeout(10):
                            await asyncio.shield(live_task)
                    except TimeoutError:
                        live_task.cancel()
                await asyncio.gather(live_task, return_exceptions=True)
            self.note_view.hide_recording()
            self.bottom_bar.set_progress(_("Saving recording…"))
            self._update_import_actions()
            saved = await session.save()
            if saved is not None:
                self._show_note(saved)
                if self._live is not None:
                    recording_id = (
                        session.journal.recording_id if session.journal is not None else ""
                    )
                    saved = await self._notes.update_transcription(
                        saved.note_id,
                        recording_id,
                        TranscriptionState.READY
                        if self._live.complete and error is None
                        else TranscriptionState.FAILED,
                        segments=self._live.segments,
                    )
                    self._show_note(saved)
                elif error is None and not self._closing:
                    recording = next(
                        r
                        for r in saved.recordings
                        if session.journal is not None
                        and r.recording_id == session.journal.recording_id
                    )
                    await self._begin_recording_upload(saved.note_id, recording, choice, options)
            elif error is None:
                self.add_toast(Adw.Toast(title=_("No audio was recorded")))
            if error is not None:
                log.warning("Microphone capture stopped: %s", error)
                show_error(self, str(error))
        finally:
            if live_task is not None and not live_task.done():
                live_task.cancel()
                await asyncio.gather(live_task, return_exceptions=True)
            self._live = None
            if self._session is not None:
                await self._session.stop()
            self._session = None
            self._recording_note_id = None
            self._recording_task = None
            self.note_view.hide_recording()
            self.bottom_bar.set_progress(None)
            self._refresh_recording_sidebar()
            self._show_note(self._note)

    def _refresh_recording_sidebar(self) -> None:
        self._start_search()
        self._select_row(self._note.note_id if self._note else None)

    def _on_live_update(self, snapshot: LiveSnapshot) -> None:
        self.note_view.update_live(snapshot)

    def _on_capture_progress(self, progress: CaptureProgress) -> None:
        self.bottom_bar.update_recording(progress)
        self.note_view.recording_view.set_state(progress.state)
        if progress.state is not CaptureState.STOPPED:
            self.window_title.set_subtitle(
                _("Paused") if progress.state is CaptureState.PAUSED else _("Recording")
            )
        self._update_import_actions()

    def _on_pause_recording(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._session is not None:
            run_task(self._change_capture_state(True), self)

    def _on_resume_recording(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._session is not None:
            run_task(self._change_capture_state(False), self)

    async def _change_capture_state(self, pause: bool) -> None:
        if self._session is None:
            return
        if pause:
            await self._session.pause()
        else:
            await self._session.resume()
        self._refresh_recording_sidebar()

    def _on_stop_recording(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._session is not None:
            run_task(self._session.stop(), self)

    def _on_retry_recording(self, _action: Gio.SimpleAction, param: GLib.Variant | None) -> None:
        choice, note = self.bottom_bar.choice, self._note
        if (
            choice is None
            or note is None
            or param is None
            or self._import_task is not None
            or self._recording_task is not None
        ):
            return
        recording = note.recording(param.get_string())
        if recording is None or recording.transcription_state is TranscriptionState.READY:
            return
        options = self._transcription_options(self.bottom_bar.file_features)
        self._recording_task = run_task(
            self._retry_recording(note.note_id, recording, choice, options), self
        )
        self._update_import_actions()

    async def _retry_recording(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        try:
            await self._begin_recording_upload(note_id, recording, choice, options)
        finally:
            self._recording_task = None
            self.bottom_bar.set_progress(None)
            self._update_import_actions()

    async def _begin_recording_upload(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        if self._closing:
            return
        self._import_task = run_task(
            self._upload_recording(note_id, recording, choice, options), self
        )
        self.bottom_bar.set_progress(_("Transcribing…"))
        self._update_import_actions()
        try:
            await self._import_task
        except asyncio.CancelledError:
            self.add_toast(Adw.Toast(title=_("Transcription cancelled")))
        finally:
            self._import_task = None

    async def _upload_recording(
        self, note_id: str, recording: Recording, choice: ModelChoice, options: TranscriptionOptions
    ) -> None:
        self._begin_file_transcription(recording, choice, replacing=True)
        try:
            key = await self._providers.get_api_key(choice.provider.provider_id)
            note = await transcribe_recording(
                note_id,
                recording,
                choice,
                client=create_client(choice.provider, key),
                notes=self._notes,
                options=options,
                on_progress=self._on_file_progress,
                on_update=self._on_file_update,
            )
        except USER_FACING_ERRORS as error:
            await self._notes.update_transcription(
                note_id, recording.recording_id, TranscriptionState.FAILED
            )
            self._show_import_failure(str(error))
            return
        finally:
            self._end_file_transcription()
        if self._note is not None and self._note.note_id == note_id:
            self._show_note(note)

    async def _offer_recovery(self) -> None:
        store = CaptureStore(await asyncio.to_thread(capture_dir))
        journals = await asyncio.to_thread(store.pending)
        if not journals:
            return
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
        dialog.present(self)
        try:
            response = await future
        finally:
            dialog.close()
        if response != "recover":
            return
        self._recovery_busy = True
        self.bottom_bar.set_progress(_("Recovering recordings…"))
        self._update_import_actions()
        try:
            for journal in journals:
                note = await recover_recording(journal, store, self._notes)
                if note is not None:
                    self._show_note(note)
                    self._select_row(note.note_id)
                    self.split_view.set_show_content(True)
        finally:
            self._recovery_busy = False
            self.bottom_bar.set_progress(None)
            self._update_import_actions()

    @Gtk.Template.Callback()
    def _on_window_close_request(self, _window: Gtk.Window) -> bool:
        if self._closing:
            return True
        self._playback.stop()
        self.note_view.flush_edits()
        self._search.close()
        recovery_pending = self._recovery_task is not None and not self._recovery_task.done()
        if (
            self._recording_task is None
            and self._import_task is None
            and not recovery_pending
            and not self._playback.pending
            and not self._editing.pending
        ):
            return False
        self._closing = True
        run_task(self._save_and_close(), self)
        return True

    async def _save_and_close(self) -> None:
        try:
            await self._editing.wait_saved()
            await self._playback.finish()
            task = self._recording_task
            if self._session is not None:
                await self._session.stop()
            importing = self._import_task
            if importing is not None:
                importing.cancel()
            recovery = self._recovery_task
            if recovery is not None and not recovery.done():
                if not self._recovery_busy:
                    recovery.cancel()
                try:
                    await recovery
                except asyncio.CancelledError:
                    if not recovery.cancelled():
                        raise
            if task is not None:
                await task
            elif importing is not None:
                try:
                    await importing
                except asyncio.CancelledError:
                    if not importing.cancelled():
                        raise
            self.destroy()
        except USER_FACING_ERRORS:
            self._closing = False
            self._update_import_actions()
            raise


def _from_ms(value: int) -> timedelta | None:
    return None if value == NO_TIME else timedelta(milliseconds=value)
