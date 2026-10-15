"""The Adw.Application: app-wide actions, styling and window management."""

import asyncio
import gc
import logging
from collections.abc import Callable
from functools import partial
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from kikoe.constants import APP_ID, APP_NAME, ISSUE_URL, VERSION, WEBSITE
from kikoe.core.preferences import WindowState, default_provider
from kikoe.i18n import _, is_right_to_left
from kikoe.paths import config_dir, data_dir, log_dir
from kikoe.providers.local.engines import load_catalog as load_local_catalog
from kikoe.providers.local.hardware import current_platform, in_flatpak
from kikoe.services.local_models import LocalModelManager
from kikoe.services.prewarm import prewarm_provider
from kikoe.storage.library import Library, audio_dir, database_path
from kikoe.storage.local_installs import LocalInstalls
from kikoe.storage.local_settings import LocalSettingsStore
from kikoe.storage.model_catalog import load_catalog
from kikoe.storage.notes import NoteRepository
from kikoe.storage.preferences import PreferencesRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.storage.secrets import KeyringSecretStore
from kikoe.storage.settings import SettingsError, SettingsStore, default_settings_path
from kikoe.storage.settings_writer import SettingsWriter
from kikoe.storage.window_state import WindowStateStore
from kikoe.ui.preferences import PreferencesDialog
from kikoe.ui.shortcuts import create_shortcuts_dialog
from kikoe.ui.tasks import run_task, show_notice
from kikoe.ui.templates import UI_DIR
from kikoe.ui.window import Window

log = logging.getLogger(__name__)


class Application(Adw.Application):
    """Owns app-level actions (``app.*``). Window-level actions live on the window."""

    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._settings = SettingsStore(default_settings_path())
        writer = SettingsWriter(self._settings, load_catalog())
        self.providers = ProviderRepository(writer, KeyringSecretStore())
        self.preferences = PreferencesRepository(writer)
        self.window_state = WindowStateStore(writer)
        self.notes = NoteRepository(Library(database_path(), audio_dir()))
        self.local_models = LocalModelManager(
            load_local_catalog(),
            current_platform(),
            LocalInstalls(data_dir() / "local"),
            LocalSettingsStore(config_dir() / "local.json"),
            sandboxed=in_flatpak(),
        )
        self._add_action("quit", self._on_quit, ["<primary>q"])
        self._add_action("preferences", self._present_preferences, ["<primary>comma"])
        self._add_action("add-provider", partial(self._present_preferences, add_provider=True))
        self._add_action("manage-providers", partial(self._present_preferences, "providers"))
        self._add_action("about", self._on_about)
        self._add_action("shortcuts", self._on_shortcuts, ["<primary>question"])
        self.set_accels_for_action("window.close", ["<primary>w"])
        self._add_action("open-data-folder", partial(self._open_folder, data_dir))
        self._add_action("open-log-folder", partial(self._open_folder, log_dir))

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Gtk.Widget.set_default_direction(
            Gtk.TextDirection.RTL if is_right_to_left() else Gtk.TextDirection.LTR
        )
        self._load_style()

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = Window(
                self,
                self.providers,
                self.preferences,
                self.window_state,
                self.notes,
                self._saved_window_state(),
            )
            run_task(self._load_settings(), window)
            run_task(self.notes.load(), window)
            run_task(self.local_models.load(), window)
        window.present()

    async def _load_settings(self) -> None:
        """Providers are announced first, then the defaults that choose among them."""
        await self.providers.load()
        await self.preferences.load()
        await self._prewarm_default_provider()

    async def _prewarm_default_provider(self) -> None:
        """Load the SDK the next transcription will need while the user is still looking around."""
        provider = default_provider(self.preferences.current, self.providers.providers)
        if provider is not None:
            await prewarm_provider(provider)
        # The SDK objects just loaded live as long as the process; see ``main``.
        gc.freeze()

    def _saved_window_state(self) -> WindowState | None:
        """Read synchronously: the size must be known before the first frame is shown."""
        try:
            return self._settings.load_state().window
        except SettingsError as error:
            log.warning("Starting with the default window size: %s", error)
            return None

    def _add_action(
        self, name: str, callback: Callable[[], None], accels: list[str] | None = None
    ) -> None:
        """Add ``app.<name>``; none of the app actions take a parameter."""
        action = Gio.SimpleAction.new(name, None)

        def activate(_action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
            callback()

        action.connect("activate", activate)
        self.add_action(action)
        if accels:
            self.set_accels_for_action(f"app.{name}", accels)

    def _load_style(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            log.warning("No display; skipping icons and CSS")
            return
        # Bundled icons live inside the package so they work on every platform.
        Gtk.IconTheme.get_for_display(display).add_search_path(str(UI_DIR / "icons"))
        provider = Gtk.CssProvider()
        provider.load_from_path(str(UI_DIR / "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _on_quit(self) -> None:
        window = self.get_active_window()
        if window is not None:
            window.close()
        else:
            self.quit()

    def _present_preferences(self, page: str | None = None, add_provider: bool = False) -> None:
        dialog = PreferencesDialog(self.providers, self.preferences, self.local_models)
        if page is not None:
            dialog.set_visible_page_name(page)
        dialog.present(self.get_active_window())
        if add_provider:
            dialog.start_add_provider()

    def _on_shortcuts(self) -> None:
        create_shortcuts_dialog().present(self.get_active_window())

    def _open_folder(self, folder: Callable[[], Path]) -> None:
        window = self.get_active_window()
        if window is not None:
            run_task(self._launch_folder(folder), window)

    async def _launch_folder(self, folder: Callable[[], Path]) -> None:
        path = await asyncio.to_thread(folder)
        launcher = Gtk.FileLauncher.new(Gio.File.new_for_path(str(path)))
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        def finished(_source: GObject.Object | None, result: Gio.AsyncResult) -> None:
            try:
                launcher.launch_finish(result)
            except GLib.Error:
                if not future.done():
                    future.set_exception(OSError(_("Could not open the folder.")))
            else:
                if not future.done():
                    future.set_result(None)

        launcher.launch(self.get_active_window(), None, finished)
        try:
            await future
        except OSError as error:
            window = self.get_active_window()
            if window is not None:
                show_notice(window, str(error))

    def _on_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            license_type=Gtk.License.GPL_3_0,
            copyright="© 2026 first-storm",
            website=WEBSITE,
            issue_url=ISSUE_URL,
            developer_name="first-storm",
            developers=["first-storm"],
            # Translators: replace with your name(s), one per line.
            translator_credits=_("translator-credits"),
        )
        about.present(self.get_active_window())
