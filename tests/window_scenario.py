"""Drive a real main window in its own process and print what happened as JSON.

Run by ``test_window.py``. It needs its own process because the GLib event loop policy
is process-wide and would break the other tests' ``asyncio.run``.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.events import GLibEventLoopPolicy  # noqa: E402
from gi.repository import Adw, Gio, GLib  # noqa: E402

from kikoe import i18n  # noqa: E402
from kikoe.core.models import ApiKind  # noqa: E402
from kikoe.storage.library import Library  # noqa: E402
from kikoe.storage.model_catalog import load_catalog  # noqa: E402
from kikoe.storage.notes import NoteRepository  # noqa: E402
from kikoe.storage.preferences import PreferencesRepository  # noqa: E402
from kikoe.storage.providers import ProviderRepository  # noqa: E402
from kikoe.storage.settings import SettingsStore  # noqa: E402
from kikoe.storage.settings_writer import SettingsWriter  # noqa: E402
from kikoe.storage.window_state import WindowStateStore  # noqa: E402

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
    window.activate_action("win.new-note", None)
    await settle()
    created = window.note
    result["new_note_action_creates_and_selects"] = (
        created is not None
        and len(notes.notes) == 1
        and created.note_id == notes.notes[0].note_id
        and window.notes_list.get_selected_row() is not None
    )
    if created is not None:
        await notes.delete_note(created.note_id)
    await settle()
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
