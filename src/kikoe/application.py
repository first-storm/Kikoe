"""The Adw.Application: app-wide actions, styling and window management."""

import asyncio
import logging
from collections.abc import Callable

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from kikoe.constants import APP_ID, APP_NAME, ISSUE_URL, VERSION, WEBSITE
from kikoe.core.preferences import WindowState
from kikoe.i18n import _
from kikoe.paths import data_dir, log_dir
from kikoe.storage.library import Library, audio_dir, database_path
from kikoe.storage.notes import NoteRepository
from kikoe.storage.providers import ProviderRepository
from kikoe.storage.secrets import KeyringSecretStore
from kikoe.storage.settings import SettingsError, SettingsStore, default_settings_path
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
        self.providers = ProviderRepository(self._settings, KeyringSecretStore())
        self.notes = NoteRepository(Library(database_path(), audio_dir()))
        self._add_action("quit", self._on_quit, ["<primary>q"])
        self._add_action("preferences", self._on_preferences, ["<primary>comma"])
        self._add_action("add-provider", self._on_add_provider)
        self._add_action("manage-providers", self._on_manage_providers)
        self._add_action("about", self._on_about)
        self._add_action("shortcuts", self._on_shortcuts, ["<primary>question"])
        self.set_accels_for_action("window.close", ["<primary>w"])
        self._add_action("open-data-folder", self._on_open_data_folder)
        self._add_action("open-log-folder", self._on_open_log_folder)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self._load_style()

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = Window(self, self.providers, self.notes, self._saved_window_state())
            run_task(self.providers.load(), window)
            run_task(self.notes.load(), window)
        window.present()

    def _saved_window_state(self) -> WindowState | None:
        """Read synchronously: the size must be known before the first frame is shown."""
        try:
            return self._settings.load_state().window
        except SettingsError as error:
            log.warning("Starting with the default window size: %s", error)
            return None

    def _add_action(
        self,
        name: str,
        callback: Callable[[Gio.SimpleAction, GLib.Variant | None], None],
        accels: list[str] | None = None,
    ) -> None:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
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

    def _on_quit(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        window = self.get_active_window()
        if window is not None:
            window.close()
        else:
            self.quit()

    def _on_preferences(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        PreferencesDialog(self.providers).present(self.get_active_window())

    def _on_manage_providers(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        dialog = PreferencesDialog(self.providers)
        dialog.set_visible_page_name("providers")
        dialog.present(self.get_active_window())

    def _on_add_provider(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        dialog = PreferencesDialog(self.providers)
        dialog.present(self.get_active_window())
        dialog.start_add_provider()

    def _on_shortcuts(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        create_shortcuts_dialog().present(self.get_active_window())

    def _on_open_data_folder(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        window = self.get_active_window()
        if window is not None:
            run_task(self._open_folder(logs=False), window)

    def _on_open_log_folder(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        window = self.get_active_window()
        if window is not None:
            run_task(self._open_folder(logs=True), window)

    async def _open_folder(self, *, logs: bool) -> None:
        path = await asyncio.to_thread(log_dir if logs else data_dir)
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

    def _on_about(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
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
