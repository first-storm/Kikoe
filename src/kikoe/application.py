"""The Adw.Application: app-wide actions, styling and window management."""

import logging
from collections.abc import Callable

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from kikoe.constants import APP_ID, APP_NAME, ISSUE_URL, VERSION, WEBSITE
from kikoe.i18n import _
from kikoe.storage.providers import ProviderRepository
from kikoe.storage.secrets import KeyringSecretStore
from kikoe.storage.settings import SettingsStore, default_settings_path
from kikoe.ui.preferences import PreferencesDialog
from kikoe.ui.tasks import run_task
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
        self.providers = ProviderRepository(
            SettingsStore(default_settings_path()), KeyringSecretStore()
        )
        self._add_action("quit", self._on_quit, ["<primary>q"])
        self._add_action("preferences", self._on_preferences, ["<primary>comma"])
        self._add_action("add-provider", self._on_add_provider)
        self._add_action("about", self._on_about)

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self._load_style()

    def do_activate(self) -> None:
        window = self.get_active_window()
        if window is None:
            window = Window(application=self, repository=self.providers)
            run_task(self.providers.load(), window)
        window.present()

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
        self.quit()

    def _on_preferences(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        PreferencesDialog(self.providers).present(self.get_active_window())

    def _on_add_provider(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        dialog = PreferencesDialog(self.providers)
        dialog.present(self.get_active_window())
        dialog.start_add_provider()

    def _on_about(self, _action: Gio.SimpleAction, _param: GLib.Variant | None) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            website=WEBSITE,
            issue_url=ISSUE_URL,
            developer_name="first-storm",
            developers=["first-storm"],
            # Translators: replace with your name(s), one per line.
            translator_credits=_("translator-credits"),
        )
        about.present(self.get_active_window())
