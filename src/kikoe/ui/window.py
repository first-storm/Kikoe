"""The main window: notes sidebar on the left, the selected note on the right."""

import logging
from collections.abc import Callable
from functools import partial

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from kikoe.core.activity import Activity, action_availability
from kikoe.core.models import DisplayOptions, Note
from kikoe.core.preferences import WindowState
from kikoe.core.recording import CaptureState
from kikoe.i18n import _
from kikoe.media.player import PlaybackError
from kikoe.services.activity import ActivityTracker
from kikoe.services.errors import USER_FACING_ERRORS
from kikoe.services.file_transcription import ClientFactory
from kikoe.services.playback import Playback
from kikoe.storage.notes import ContentChanged, NoteEvent, NoteRepository, SummariesChanged
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.storage.window_state import WindowStateStore
from kikoe.ui.bottom_bar import BottomBar
from kikoe.ui.export_controller import ExportController
from kikoe.ui.labels import note_title_subtitle
from kikoe.ui.note_actions import NoteActions
from kikoe.ui.note_sidebar import NoteSidebar
from kikoe.ui.note_view import NoteView
from kikoe.ui.recording_controller import RecordingController
from kikoe.ui.segment_editor import SegmentEditor
from kikoe.ui.speaker_controller import SpeakerController
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file
from kikoe.ui.transcription_controller import TranscriptionController
from kikoe.ui.window_chrome import GeometryKeeper, HeaderLayout

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
    compact_breakpoint: Adw.Breakpoint = Gtk.Template.Child()
    split_view: Adw.NavigationSplitView = Gtk.Template.Child()
    sidebar_header: Adw.HeaderBar = Gtk.Template.Child()
    new_note_button: Gtk.Button = Gtk.Template.Child()
    content_header: Adw.HeaderBar = Gtk.Template.Child()
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
        preferences: PreferencesRepository,
        window_store: WindowStateStore,
        notes: NoteRepository,
        clients: ClientFactory,
        window_state: WindowState | None = None,
    ) -> None:
        super().__init__(application=application)
        self._clients = clients
        self._providers = providers
        self._preferences = preferences
        self._notes = notes
        self._note: Note | None = None
        self._providers_loaded = False
        self._recovery_checked = False
        self._work = ActivityTracker()
        self._geometry = GeometryKeeper(self, window_store, window_state)
        self._create_views()
        self._create_controllers()
        self._register_actions(application)
        self.set_action_enabled("new-note", False)  # Until providers are loaded.
        self.show_note(None)
        providers.connect_changed(self._on_providers_changed)
        notes.connect_changed(self._on_notes_changed)
        self.bottom_bar.connect("choice-changed", self._on_bottom_bar_choice_changed)
        self.bottom_bar.bind(providers, preferences)
        self._header_layout = HeaderLayout(
            self,
            self.sidebar_header,
            self.content_header,
            self.new_note_button,
            self.compact_breakpoint,
        )

    def _create_views(self) -> None:
        # Custom widgets are added from code: Blueprint's linter cannot inspect their types.
        self.note_view = NoteView()
        self.note_view_slot.set_child(self.note_view)
        self.bottom_bar = BottomBar()
        self.bottom_bar_slot.set_child(self.bottom_bar)

    def _create_controllers(self) -> None:
        """Each controller owns one kind of work; the window only wires them together."""
        notes, providers, preferences = self._notes, self._providers, self._preferences
        start_task = partial(run_task, parent=self)
        self._editor = SegmentEditor(self, notes, self.note_view, start_task)
        self._export = ExportController(
            self, self, self.note_view.flush_edits, self._editor.wait_saved
        )
        self._sidebar = NoteSidebar(
            notes=notes,
            notes_list=self.notes_list,
            placeholder=self.notes_placeholder,
            search_entry=self.search_entry,
            search_bar=self.search_bar,
            split_view=self.split_view,
            start_task=start_task,
            current_note_id=lambda: self._note.note_id if self._note else None,
            capture_state=self._sidebar_capture_state,
            locked=self._switch_locked,
            open_note=self._open_from_sidebar,
        )
        self._playback = Playback(start_task, self.note_view.set_playing, self._on_playback_error)
        self._speakers = SpeakerController(self, self, notes, preferences, self._playback)
        self._transcribe = TranscriptionController(
            self,
            self,
            providers=providers,
            preferences=preferences,
            notes=notes,
            work=self._work,
            bottom_bar=self.bottom_bar,
            playback=self._playback,
            speakers=self._speakers,
            note_view=self.note_view,
            clients=self._clients,
        )
        self._recorder = RecordingController(
            self,
            self,
            providers=providers,
            preferences=preferences,
            notes=notes,
            work=self._work,
            bottom_bar=self.bottom_bar,
            playback=self._playback,
            sidebar=self._sidebar,
            note_view=self.note_view,
            transcribe=self._transcribe,
            clients=self._clients,
        )
        self._note_actions = NoteActions(self, self, notes, self._playback)

    def _register_actions(self, application: Gtk.Application) -> None:
        """Buttons and menus trigger these `win.*` actions; each belongs to one controller."""
        string = GLib.VariantType.new("s")
        for name, handler, parameter in (
            ("search-notes", self._on_search_notes, None),
            ("choose-model", self._on_choose_model, None),
            ("new-note", self._note_actions.on_new_note, None),
            ("rename-note", self._note_actions.on_rename_note, None),
            ("delete-note", self._note_actions.on_delete_note, None),
            ("rename-note-row", self._note_actions.on_rename_note, string),
            ("delete-note-row", self._note_actions.on_delete_note, string),
            ("delete-recording", self._note_actions.on_delete_recording, string),
            ("copy-all-text", self._export.on_copy_all_text, None),
            ("export", self._export.on_export, None),
            ("export-audio", self._export.on_export_audio, string),
            ("play-recording", self._speakers.on_play_recording, string),
            ("play-sample", self._speakers.on_play_sample, GLib.VariantType.new("(sdd)")),
            ("edit-speaker", self._speakers.on_edit_speaker, GLib.VariantType.new("(ss)")),
            ("match-speakers", self._speakers.on_match_speakers, string),
            ("undo", self._editor.on_undo, None),
            ("redo", self._editor.on_redo, None),
            ("import-audio", self._transcribe.on_import_audio, None),
            ("cancel-import", self._transcribe.on_cancel_import, None),
            ("retry-recording", self._transcribe.on_retry_recording, string),
            ("retranscribe-recording", self._transcribe.on_retranscribe_recording, string),
            ("start-recording", self._recorder.on_start, None),
            ("pause-recording", self._recorder.on_pause, None),
            ("resume-recording", self._recorder.on_resume, None),
            ("stop-recording", self._recorder.on_stop, None),
        ):
            self._add_action(name, handler, parameter)
        self._add_toggle_action("show-timestamps")
        self._add_toggle_action("show-speakers")
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

    @property
    def note(self) -> Note | None:
        """The note shown in the content pane."""
        return self._note

    def add_toast(self, toast: Adw.Toast) -> None:
        self.toast_overlay.add_toast(toast)

    def toast(self, title: str) -> None:
        self.add_toast(Adw.Toast(title=title))

    def show_content_pane(self) -> None:
        self.split_view.set_show_content(True)

    def set_subtitle(self, subtitle: str) -> None:
        self.window_title.set_subtitle(subtitle)

    def select_note(self, note_id: str) -> None:
        """Select ``note_id`` in the sidebar and show it."""
        if self._sidebar.show_all_and_select(note_id):
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

    @Gtk.Template.Callback()
    def _on_window_notify_current_breakpoint(
        self, _window: Adw.ApplicationWindow, _spec: GObject.ParamSpec
    ) -> None:
        self._header_layout.sync()

    # Sidebar

    def _on_search_notes(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._sidebar.start_search()

    @Gtk.Template.Callback()
    def _on_search_entry_stop_search(self, _entry: Gtk.SearchEntry) -> None:
        self._sidebar.stop_search()

    @Gtk.Template.Callback()
    def _on_search_bar_notify_search_mode_enabled(
        self, _bar: Gtk.SearchBar, _spec: GObject.ParamSpec
    ) -> None:
        self._sidebar.search_mode_changed()

    @Gtk.Template.Callback()
    def _on_search_entry_search_changed(self, _entry: Gtk.SearchEntry) -> None:
        self._sidebar.refresh()

    @Gtk.Template.Callback()
    def _on_notes_list_row_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        self._sidebar.row_selected(row)

    def _open_from_sidebar(self, note_id: str) -> None:
        run_task(self._open_note(note_id), self)
        self.split_view.set_show_content(True)

    def _sidebar_capture_state(self, note_id: str) -> CaptureState:
        session = self._recorder.session
        if session is not None and note_id == self._recorder.note_id:
            return session.state
        return CaptureState.STOPPED

    def _activity(self) -> Activity:
        """What the window is doing right now; every action state follows from it."""
        choice = self.bottom_bar.choice
        session = self._recorder.session
        return Activity(
            importing=self._work.import_task is not None,
            recording=self._work.recording_task is not None,
            recovering=self._work.recovery_busy,
            committing=self._work.committing,
            cancelling=self._work.cancelling,
            stopping=self._work.stopping,
            closing=self._work.closing,
            capture_state=session.state if session is not None else CaptureState.STOPPED,
            has_note=self._note is not None,
            viewing_transcription=self._viewing_transcription(),
            has_model=choice is not None,
            model_transcribes_files=choice is not None
            and choice.model.capabilities.file is not None,
            has_providers=bool(self._providers.providers),
        )

    def _switch_locked(self) -> bool:
        """Whether the open note must stay put; transcription alone never pins it."""
        return self._activity().switch_locked

    def _viewing_transcription(self) -> bool:
        """Whether the note on screen is the one a running transcription writes to."""
        if self._work.import_task is None:
            return False
        return self._note is None or self._note.note_id == self._transcribe.display.note_id

    def _on_notes_changed(self, event: NoteEvent) -> None:
        if not self._recovery_checked:
            self._recovery_checked = True
            self._work.recovery_task = run_task(self._recorder.offer_recovery(), self)
        match event:
            case SummariesChanged(note_id):
                self._sidebar.refresh()
                self._follow_summaries(note_id)
            case ContentChanged(note_id):
                if self._note is not None and note_id == self._note.note_id:
                    run_task(self._open_note(note_id), self)

    def _follow_summaries(self, note_id: str | None) -> None:
        """Keep the content pane on a note that still exists, reloading it if it changed."""
        current = self._note.note_id if self._note else None
        if current is None:
            self.refresh_content()
            return
        if not any(s.note_id == current for s in self._notes.notes):
            self.show_note(None)
            self.split_view.set_show_content(False)
        elif note_id == current:
            run_task(self._open_note(current), self)

    def _on_providers_changed(self) -> None:
        self._providers_loaded = True
        # Matches the first-run design: nothing to transcribe with, so no new notes yet.
        self.set_action_enabled("new-note", bool(self._providers.providers))
        self.refresh_content()
        self.refresh_actions()

    def set_action_enabled(self, name: str, enabled: bool) -> None:
        action = self.lookup_action(name)
        if isinstance(action, Gio.SimpleAction):
            action.set_enabled(enabled)

    # Content

    async def _open_note(self, note_id: str) -> None:
        note = await self._editor.read_note(note_id)
        current = self._recorder.note_id or (self._note.note_id if self._note else None)
        if not self._switch_locked() or note_id == current:
            self.show_note(note)

    def show_note(self, note: Note | None) -> None:
        self._transcribe.display.hide_if_other(note)
        if self._note is not None and (note is None or self._note.note_id != note.note_id):
            self._playback.stop()
            # Saved under the note being left, so this must run before it is replaced.
            self.note_view.flush_edits()
            self._editor.forget()
        note = self._editor.set_note(note)
        self._note = note
        self.note_menu_button.set_visible(note is not None)
        self.display_options_button.set_visible(note is not None)
        for name in _NOTE_ACTIONS:
            self.set_action_enabled(name, note is not None)
        if note is None:
            self.set_titles(_("Kikoe"), "", "")
        else:
            self.set_titles(note.title, note.title, note_title_subtitle(note))
            self._set_display_action_state("show-timestamps", note.display.show_timestamps)
            self._set_display_action_state("show-speakers", note.display.show_speakers)
            if self.note_view.show_note(note):
                self._editor.forget()
            self._transcribe.display.show(note)
            self.bottom_bar.set_note(bool(note.recordings))
            self._recorder.show_in_progress()
        self.refresh_content()
        self.refresh_actions()

    def set_titles(self, page: str, title: str, subtitle: str) -> None:
        self.content_page.set_title(page)
        self.window_title.set_title(title)
        self.window_title.set_subtitle(subtitle)

    def apply_segment_edit(self, note: Note, recording_id: str, position: int) -> None:
        """Show an edit of the open note's segment; nothing else about the note changed."""
        if self._note is None or self._note.note_id != note.note_id:
            return
        self._note = note
        self.note_view.update_segment(note, recording_id, position)

    def refresh_content(self) -> None:
        if self._note is not None or self._transcribe.display.active:
            page = "note"
        elif not self._providers_loaded:
            page = "loading"
        elif not self._providers.providers:
            page = "welcome"
        else:
            page = "empty"
        self.content_stack.set_visible_child_name(page)
        self.bottom_bar_slot.set_visible(page == "note")

    # Playback

    def _on_playback_error(self, error: PlaybackError) -> None:
        log.warning("Playback failed: %s", error)
        self.toast(str(error))

    # Import

    def _on_bottom_bar_choice_changed(self, _bar: BottomBar) -> None:
        self.refresh_actions()

    def refresh_actions(self) -> None:
        activity = self._activity()
        if activity.locked:
            self._playback.stop()
        for name, enabled in action_availability(activity).items():
            self.set_action_enabled(name, enabled)
        self.notes_list.set_can_target(not activity.switch_locked)
        self.search_button.set_sensitive(not activity.busy)
        # Typing in the list starts a search, which must not hide the Stop controls.
        self._sidebar.allow_typing_to_search(not activity.busy)
        self._editor.blocked = activity.locked
        self.note_view.set_editable(not activity.locked)
        self._editor.update_actions()

    def _on_choose_model(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self.bottom_bar.present_transcription_dialog()

    @Gtk.Template.Callback()
    def _on_window_close_request(self, _window: Gtk.Window) -> bool:
        if self._work.closing:
            return True
        self._playback.stop()
        self.note_view.flush_edits()
        self._sidebar.close()
        if (
            not self._work.has_pending_work()
            and not self._playback.pending
            and not self._editor.pending
        ):
            return False
        self._work.closing = True
        run_task(self._save_and_close(), self)
        return True

    async def _save_and_close(self) -> None:
        try:
            await self._work.wind_down(
                wait_edits=self._editor.wait_saved,
                finish_playback=self._playback.finish,
                stop_recording=self._recorder.stop,
            )
            self.destroy()
        except USER_FACING_ERRORS:
            self._work.closing = False
            self.refresh_actions()
            raise
