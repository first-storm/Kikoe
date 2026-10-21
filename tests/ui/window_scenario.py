"""Drive a real main window in its own process and print what happened as JSON.

Run by ``test_window.py``. It needs its own process because the GLib event loop policy
is process-wide and would break the other tests' ``asyncio.run``.
"""

import asyncio
import json
import sys
import tempfile
import threading
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.events import GLibEventLoopPolicy  # noqa: E402
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from kikoe import i18n  # noqa: E402
from kikoe.core.models import ApiKind, Note, NoteSummary  # noqa: E402
from kikoe.storage.errors import NoteNotFoundError  # noqa: E402
from kikoe.storage.library import Library  # noqa: E402
from kikoe.storage.model_catalog import load_catalog  # noqa: E402
from kikoe.storage.notes import NoteRepository  # noqa: E402
from kikoe.storage.preferences import PreferencesRepository  # noqa: E402
from kikoe.storage.providers import ProviderRepository  # noqa: E402
from kikoe.storage.settings import SettingsStore  # noqa: E402
from kikoe.storage.settings_writer import SettingsWriter  # noqa: E402
from kikoe.storage.window_state import WindowStateStore  # noqa: E402
from kikoe.ui.new_note_dialog import NewNoteDialog  # noqa: E402

if TYPE_CHECKING:
    from kikoe.ui.window import Window


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        return self.values.get(provider_id)

    def set(self, provider_id: str, api_key: str) -> None:
        self.values[provider_id] = api_key

    def delete(self, provider_id: str) -> None:
        self.values.pop(provider_id, None)


ACTIONS = (
    "new-note",
    "rename-note",
    "copy-all-text",
    "delete-note",
    "rename-note-row",
    "delete-note-row",
    "export",
    "show-timestamps",
    "show-speakers",
    "export-audio",
    "choose-model",
    "import-audio",
    "cancel-import",
    "start-recording",
    "pause-recording",
    "resume-recording",
    "stop-recording",
    "retry-recording",
    "play-recording",
    "play-sample",
    "edit-speaker",
    "undo",
    "redo",
    "match-speakers",
    "retranscribe-recording",
    "delete-recording",
    "search-notes",
)


async def settle(seconds: float = 0.4) -> None:
    await asyncio.sleep(seconds)


async def new_note_scenario(
    window: Window, notes: NoteRepository, result: dict[str, object]
) -> None:
    window.activate_action("win.new-note", None)
    await settle()
    dialog = window.get_visible_dialog()
    assert isinstance(dialog, NewNoteDialog)
    result["new_note_opens_without_saving"] = not notes.notes
    focus = dialog.get_focus()
    result["new_note_prefills_and_selects_title"] = (
        dialog.title_row.get_text() == "New Note"
        and dialog.title_row.get_selection_bounds() == (0, len("New Note"))
        and focus is not None
        and focus.is_ancestor(dialog.title_row)
    )
    for title in ("", "   "):
        dialog.title_row.set_text(title)
        await settle(0.05)
        result["new_note_rejects_blank_titles"] = not dialog.create_button.is_sensitive()
        assert result["new_note_rejects_blank_titles"]
    dialog.close()
    await settle()
    result["new_note_cancel_does_not_save"] = not notes.notes
    window.activate_action("win.new-note", None)
    await settle()
    dialog = window.get_visible_dialog()
    assert isinstance(dialog, NewNoteDialog)
    focus = dialog.get_focus()
    assert focus is not None
    focus.emit("activate")
    await settle()
    created = window.note
    result["new_note_action_creates_and_selects"] = (
        created is not None
        and len(notes.notes) == 1
        and created.note_id == notes.notes[0].note_id
        and window.notes_list.get_selected_row() is not None
        and created.title == "New Note"
    )
    if created is not None:
        await notes.delete_note(created.note_id)
    await settle()
    window.activate_action("win.new-note", None)
    await settle()
    dialog = window.get_visible_dialog()
    assert isinstance(dialog, NewNoteDialog)
    dialog.title_row.set_text("  Team meeting  ")
    dialog.create_button.activate()
    await settle()
    created = window.note
    result["new_note_saves_trimmed_custom_title"] = (
        created is not None and created.title == "Team meeting" and len(notes.notes) == 1
    )
    if created is not None:
        await notes.delete_note(created.note_id)
    await settle()


async def scenario(
    window: Window,
    providers: ProviderRepository,
    notes: NoteRepository,
    result: dict[str, object],
) -> None:
    result["missing_actions"] = [name for name in ACTIONS if window.lookup_action(name) is None]
    new_note = window.lookup_action("new-note")
    result["new_note_waits_for_a_provider"] = (
        isinstance(new_note, Gio.SimpleAction) and not new_note.get_enabled()
    )
    await providers.add_provider("Test", ApiKind.OPENAI, "https://example.test/v1", "key")
    await settle()
    result["new_note_enabled_with_a_provider"] = (
        isinstance(new_note, Gio.SimpleAction) and new_note.get_enabled()
    )
    await new_note_scenario(window, notes, result)
    await new_note_retry_scenario(window, result)
    await notes.create_note("Alpha meeting")
    await notes.create_note("Beta call")
    await settle()
    result["lists_both_notes"] = (
        window.notes_list.get_row_at_index(1) is not None
        and window.notes_list.get_row_at_index(2) is None
    )
    first = notes.notes[0]
    window.select_note(first.note_id)
    await settle()
    result["opens_selected_note"] = window.note is not None and window.note.note_id == first.note_id
    result["highlights_selected_row"] = window.notes_list.get_selected_row() is not None
    await display_toggle_scenario(window, notes, first.note_id, result)
    await stale_reload_scenario(window, notes, result)
    await failed_read_scenario(window, notes, result)
    window.search_bar.set_search_mode(True)
    window.search_entry.set_text("Alpha")
    await settle(0.7)
    result["search_narrows_the_list"] = [
        window.notes_list.get_row_at_index(i) is not None for i in range(3)
    ] == [True, False, False]
    window.search_entry.set_text("zzz")
    await settle(0.7)
    result["no_match_placeholder"] = window.notes_placeholder.get_title() == "No Matching Notes"
    window.search_bar.set_search_mode(False)
    await settle()
    result["leaving_search_restores_the_list"] = window.notes_list.get_row_at_index(1) is not None
    await close_retry_scenario(window, result)


async def failed_read_scenario(
    window: Window, notes: NoteRepository, result: dict[str, object]
) -> None:
    """A missing note must not prevent future content notifications from refreshing the view."""
    current = window.note
    assert current is not None
    with suppress(NoteNotFoundError):
        await window._open_note("missing-note")
    display = replace(current.display, show_speakers=not current.display.show_speakers)
    await notes.set_display_options(current.note_id, display)
    await settle()
    shown = window.note
    result["failed_note_read_allows_automatic_reload"] = (
        shown is not None and shown.display == display
    )


async def close_retry_scenario(window: Window, result: dict[str, object]) -> None:
    """An unexpected save failure must leave the window able to retry closing."""
    dialog = window.get_visible_dialog()
    if dialog is not None:
        dialog.force_close()
        await settle()
    editor = window._editor
    original = editor.wait_saved

    async def importing() -> None:
        await asyncio.Event().wait()

    pending = asyncio.create_task(importing())
    window._work.import_task = pending

    async def fail() -> None:
        raise RuntimeError("Simulated shutdown bug")

    editor.wait_saved = fail  # pyright: ignore[reportAttributeAccessIssue]  # inject a shutdown failure
    window.close()
    await settle()
    result["close_failure_allows_retry"] = not window._work.closing
    error = window.get_visible_dialog()
    assert isinstance(error, Adw.AlertDialog)
    error.close()
    editor.wait_saved = original  # pyright: ignore[reportAttributeAccessIssue]  # restore the real saver
    await settle()
    window.close()
    await settle()
    result["close_retry_finishes_shutdown"] = not window.get_visible() and pending.cancelled()
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)


async def display_toggle_scenario(
    window: Window, notes: NoteRepository, note_id: str, result: dict[str, object]
) -> None:
    """Switch an option off and straight back on; the last choice must be the one saved."""
    window.activate_action("win.show-timestamps", None)
    window.activate_action("win.show-timestamps", None)
    await settle()
    saved = (await notes.get_note(note_id)).display
    result["display_toggles_keep_the_last_choice"] = saved.show_timestamps is True


async def stale_reload_scenario(
    window: Window, notes: NoteRepository, result: dict[str, object]
) -> None:
    """Click another note while a background change to the open one is being reloaded."""
    library = notes._library  # pyright: ignore[reportPrivateUsage]  # slow reads like a busy disk
    alpha = next(n for n in notes.notes if n.title == "Alpha meeting")
    beta = next(n for n in notes.notes if n.title == "Beta call")
    window.search_bar.set_search_mode(True)
    window.search_entry.set_text("a")
    await settle(0.8)

    def row_of(note_id: str) -> Gtk.ListBoxRow:
        items = window._sidebar._items  # pyright: ignore[reportPrivateUsage]  # the list model behind the rows
        ids = [item.summary.note_id for item in items]
        row = window.notes_list.get_row_at_index(ids.index(note_id))
        assert row is not None
        return row

    window.notes_list.select_row(row_of(alpha.note_id))
    await settle()
    gate_beta, gate_search = threading.Event(), threading.Event()
    read, search = library.get_note, library.search_notes
    slow = {"on": False}

    def slow_read(note_id: str) -> Note:
        note = read(note_id)
        if slow["on"] and note_id == beta.note_id:
            gate_beta.wait(5)
        return note

    def slow_search(query: str) -> tuple[NoteSummary, ...]:
        found = search(query)
        if slow["on"]:
            gate_search.wait(5)
        return found

    library.get_note = slow_read  # pyright: ignore[reportAttributeAccessIssue]  # test double
    library.search_notes = slow_search  # pyright: ignore[reportAttributeAccessIssue]  # test double
    slow["on"] = True
    window.notes_list.select_row(row_of(beta.note_id))
    await asyncio.sleep(0.2)
    await notes.rename_note(alpha.note_id, "Alpha meeting")
    await asyncio.sleep(0.2)
    gate_beta.set()
    await asyncio.sleep(0.4)
    gate_search.set()
    await settle(0.8)
    library.get_note, library.search_notes = read, search  # pyright: ignore[reportAttributeAccessIssue]  # restore
    selected = window.notes_list.get_selected_row()
    shown = window.note
    result["stale_reload_does_not_replace_the_new_note"] = (
        shown is not None
        and shown.note_id == beta.note_id
        and selected is not None
        and selected.get_index() == row_of(beta.note_id).get_index()
    )
    window.search_bar.set_search_mode(False)
    await settle()


async def new_note_retry_scenario(window: Window, result: dict[str, object]) -> None:
    """Hold a save open to verify duplicate protection and retry after a disk failure."""
    release = asyncio.Event()
    titles: list[str] = []

    async def save(title: str) -> None:
        titles.append(title)
        await release.wait()
        if len(titles) == 1:
            raise OSError("Simulated disk failure")

    dialog = NewNoteDialog(save)
    dialog.present(window)
    await settle()
    dialog.title_row.set_text("Retry title")
    dialog.activate_action("note.create", None)
    dialog.activate_action("note.create", None)
    await settle()
    result["new_note_prevents_duplicate_saves"] = (
        titles == ["Retry title"]
        and not dialog.create_button.is_sensitive()
        and not dialog.get_can_close()
    )
    release.set()
    await settle()
    error = window.get_visible_dialog()
    assert isinstance(error, Adw.AlertDialog)
    error.close()
    await settle()
    result["new_note_failure_preserves_input"] = (
        dialog.title_row.get_text() == "Retry title"
        and dialog.create_button.is_sensitive()
        and dialog.get_can_close()
    )
    dialog.activate_action("note.create", None)
    await settle()
    result["new_note_can_retry_after_failure"] = (
        titles == ["Retry title", "Retry title"] and window.get_visible_dialog() is None
    )


def main() -> None:
    asyncio.set_event_loop_policy(GLibEventLoopPolicy())  # pyright: ignore[reportArgumentType, reportDeprecated]  # PyGObject 3.58 still needs the policy API
    i18n.setup()
    directory = Path(tempfile.mkdtemp())
    app = Adw.Application(
        application_id="io.github.first_storm.KikoeScenario", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    result: dict[str, object] = {}

    def on_activate(application: Adw.Application) -> None:
        from kikoe.providers.factory import create_client
        from kikoe.ui.window import Window

        writer = SettingsWriter(SettingsStore(directory / "settings.json"), load_catalog())
        providers = ProviderRepository(writer, MemorySecrets())
        preferences = PreferencesRepository(writer)
        notes = NoteRepository(Library(directory / "library.sqlite3", directory / "audio"))
        window = Window(
            application, providers, preferences, WindowStateStore(writer), notes, create_client
        )
        window.present()

        async def run() -> None:
            try:
                await providers.load()
                await preferences.load()
                await notes.load()
                await scenario(window, providers, notes, result)
            except Exception as error:
                result["error"] = repr(error)
            finally:
                application.quit()

        application.create_asyncio_task(run())  # pyright: ignore[reportUnknownMemberType]  # PyGObject 3.58 API, untyped in the stubs

    app.connect("activate", on_activate)
    GLib.timeout_add_seconds(30, app.quit)
    app.run([])
    sys.stdout.write("RESULT " + json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
