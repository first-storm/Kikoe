"""Find and Replace in the open note: its matches, the current one, and replacing them."""

import re

from gi.repository import Adw, Gio, GLib, Gtk

from kikoe.core.find_replace import (
    FindQuery,
    InvalidPatternError,
    InvalidReplacementError,
    TextMatch,
    compile_query,
    find_matches,
    first_match_from,
    replace_matches,
)
from kikoe.core.models import Note
from kikoe.i18n import _, ngettext
from kikoe.ui.find_bar import FindBar
from kikoe.ui.note_view import NoteView
from kikoe.ui.segment_editor import SegmentEditor
from kikoe.ui.window_host import WindowHost

_OPTIONS = ("find-match-case", "find-whole-words", "find-regex")
"""Toggles in the bar's Search Options menu; they last until the app quits."""


class FindController:
    """Searches the open note for the find bar's query and replaces what it finds.

    Matches are found in the saved note, so they are found again whenever it changes.
    Changes often come many at once (Replace All, or undoing it), so that waits for the
    main loop to be idle, before the next frame is drawn.
    """

    def __init__(
        self,
        window: Adw.ApplicationWindow,
        host: WindowHost,
        bar: FindBar,
        note_view: NoteView,
        editor: SegmentEditor,
    ) -> None:
        self._window = window
        self._host = host
        self._bar = bar
        self._note_view = note_view
        self._editor = editor
        self._pattern: re.Pattern[str] | None = None
        self._matches: tuple[TextMatch, ...] = ()
        self._current: int | None = None
        self._anchor: tuple[str, int, int] | None = None
        """Where the current match is, or where to search from: recording, position, offset."""
        self._note_id: str | None = None
        self._refresh_source = 0
        for name, handler in (
            ("find-replace", self._on_find_replace),
            ("find-next", self._on_find_next),
            ("find-previous", self._on_find_previous),
            ("replace", self._on_replace),
            ("replace-all", self._on_replace_all),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            window.add_action(action)
        for name in _OPTIONS:
            action = Gio.SimpleAction.new_stateful(name, None, GLib.Variant.new_boolean(False))
            action.connect("activate", self._on_option_activate)
            window.add_action(action)
        bar.connect("query-changed", self._on_bar_query_changed)
        bar.connect("closed", self._on_bar_closed)

    def show(self, note: Note | None) -> None:
        """Follow the note on screen: search a newly shown one, or close without a note."""
        if note is None:
            self._bar.close()
            return
        if note.note_id != self._note_id:
            self._note_id = note.note_id
            self._anchor = None
        self.note_changed()

    def note_changed(self) -> None:
        """Find the matches again once the current burst of changes is over."""
        if self._bar.is_open and not self._refresh_source:
            self._refresh_source = GLib.idle_add(
                self._on_refresh_idle, priority=GLib.PRIORITY_HIGH_IDLE
            )

    def update_actions(self) -> None:
        found = self._current is not None
        self._host.set_action_enabled("find-next", found)
        self._host.set_action_enabled("find-previous", found)
        # Replacing writes to the note, which running work may be writing to as well.
        for name in ("replace", "replace-all"):
            self._host.set_action_enabled(name, found and not self._editor.blocked)

    def _query(self) -> FindQuery:
        return FindQuery(
            self._bar.query,
            match_case=self._option("find-match-case"),
            whole_words=self._option("find-whole-words"),
            regex=self._option("find-regex"),
        )

    def _option(self, name: str) -> bool:
        action = self._window.lookup_action(name)
        return action is not None and action.get_state() == GLib.Variant.new_boolean(True)

    def _refresh(self, *, reveal: bool = False) -> None:
        """Search the open note again, keeping the current match where it was."""
        self._cancel_refresh()
        note = self._host.note
        self._pattern, self._matches, self._current = None, (), None
        if not self._bar.is_open or note is None:
            self._note_view.matches.clear()
            self.update_actions()
            return
        query = self._query()
        try:
            self._pattern = compile_query(query)
        except InvalidPatternError:
            self._note_view.matches.clear()
            self._bar.show_count(_("Invalid pattern"), invalid=True)
            self.update_actions()
            return
        if self._pattern is not None:
            self._matches = find_matches(note, self._pattern)
            place = self._anchor or ("", 0, 0)
            self._current = first_match_from(note, self._matches, *place)
        self._show_current(note, reveal=reveal)

    def _show_current(self, note: Note, *, reveal: bool) -> None:
        current = None if self._current is None else self._matches[self._current]
        if current is not None:
            self._anchor = current.recording_id, current.position, current.start
        if self._current is not None:
            count = _("{current} of {total}").format(
                current=self._current + 1, total=len(self._matches)
            )
        else:
            count = _("No matches") if self._bar.query else ""
        self._bar.show_count(count)
        self._note_view.matches.show(note, self._matches, current)
        if reveal and current is not None:
            self._note_view.rows.reveal_segment(current.recording_id, current.position)
        self.update_actions()

    def _on_refresh_idle(self) -> bool:
        self._refresh_source = 0
        self._refresh()
        return False

    def _cancel_refresh(self) -> None:
        if self._refresh_source:
            GLib.source_remove(self._refresh_source)
            self._refresh_source = 0

    def _on_find_replace(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        if self._host.note is None:
            return
        focused = self._note_view.rows.focused_segment()
        if focused is not None:
            self._anchor = (*focused, 0)
        selected = self._selected_text()
        if selected and self._option("find-regex"):
            selected = re.escape(selected)
        self._bar.open(selected)
        self._refresh(reveal=True)

    def _selected_text(self) -> str | None:
        """The one-line selection in the segment holding the caret, to search for it."""
        focus = self._window.get_focus()
        if not isinstance(focus, Gtk.TextView):
            return None
        buffer = focus.get_buffer()
        bounds = buffer.get_selection_bounds()
        if not bounds:
            return None
        text = buffer.get_text(bounds[0], bounds[1], False)
        return text if text.strip() and "\n" not in text else None

    def _on_find_next(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._step(1)

    def _on_find_previous(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._step(-1)

    def _step(self, step: int) -> None:
        if self._refresh_source:
            self._refresh()
        note = self._host.note
        if note is None or self._current is None:
            return
        self._current = (self._current + step) % len(self._matches)
        self._show_current(note, reveal=True)

    def _on_replace(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._replace(every=False)

    def _on_replace_all(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        self._replace(every=True)

    def _replace(self, *, every: bool) -> None:
        if self._editor.blocked:
            return
        # Typing not saved yet must be in the note that is searched and replaced.
        self._note_view.flush_edits()
        self._refresh()
        note = self._host.note
        if note is None or self._pattern is None or self._current is None:
            return
        targets = self._matches if every else (self._matches[self._current],)
        try:
            result = replace_matches(
                note, self._pattern, targets, self._bar.replacement, regex=self._query().regex
            )
        except InvalidReplacementError:
            self._bar.mark_replacement_invalid(True)
            self._host.toast(_("The replacement text does not fit this pattern"))
            return
        if not every and result.inserted:
            # Searching goes on after the new text, so a replacement that contains the
            # query is not found and replaced again.
            inserted = result.inserted[0]
            self._anchor = inserted.recording_id, inserted.position, inserted.end
        self._editor.replace_texts(result.texts)
        self._refresh(reveal=True)
        if every and result.replaced:
            self._host.toast(
                ngettext(
                    "Replaced {count} match", "Replaced {count} matches", result.replaced
                ).format(count=result.replaced)
            )
        if result.kept and every:
            self._host.toast(
                ngettext(
                    "{count} segment was kept because it would be empty",
                    "{count} segments were kept because they would be empty",
                    result.kept,
                ).format(count=result.kept)
            )
        elif result.kept:
            self._host.toast(_("Segment text cannot be empty"))

    def _on_option_activate(self, action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        enabled = not self._option(action.get_name())
        action.set_state(GLib.Variant.new_boolean(enabled))
        if action.get_name() == "find-regex":
            self._bar.set_regex(enabled)
        self._refresh(reveal=True)

    def _on_bar_query_changed(self, _bar: FindBar) -> None:
        self._refresh(reveal=True)

    def _on_bar_closed(self, _bar: FindBar) -> None:
        current = None if self._current is None else self._matches[self._current]
        self._refresh()
        note = self._host.note
        if current is not None and note is not None and note.note_id == self._note_id:
            self._note_view.rows.focus_segment(
                current.recording_id, current.position, select=(current.start, current.end)
            )
