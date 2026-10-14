"""Window-frame behavior: remembering the size and fitting the headers to the window controls."""

import logging

from gi.repository import Adw, GLib, GObject, Gtk

from kikoe.core.decoration_layout import DecorationLayout
from kikoe.core.preferences import WindowState, valid_window_state
from kikoe.storage.settings import SettingsError
from kikoe.storage.window_state import WindowStateStore
from kikoe.ui.tasks import run_task

log = logging.getLogger(__name__)

SAVE_DELAY_MS = 1000


class GeometryKeeper:
    """Saves the window size shortly after the user stops resizing, not on every frame."""

    def __init__(
        self,
        window: Adw.ApplicationWindow,
        store: WindowStateStore,
        state: WindowState | None,
    ) -> None:
        self._window = window
        self._store = store
        self._timeout = 0
        if state is not None:
            window.set_default_size(state.width, state.height)
            if state.maximized:
                window.maximize()
        for prop in ("default-width", "default-height", "maximized"):
            window.connect(f"notify::{prop}", self._on_geometry_changed)

    def _on_geometry_changed(self, _window: Gtk.Window, _spec: GObject.ParamSpec) -> None:
        if self._timeout:
            GLib.source_remove(self._timeout)
        self._timeout = GLib.timeout_add(SAVE_DELAY_MS, self._start_save)

    def _start_save(self) -> bool:
        self._timeout = 0
        run_task(self._save(), self._window)
        return GLib.SOURCE_REMOVE

    async def _save(self) -> None:
        width, height = self._window.get_default_size()
        state = WindowState(width, height, self._window.is_maximized())
        if not valid_window_state(state):
            return
        try:
            await self._store.update(state)
        except SettingsError as error:
            log.warning("Could not remember the window size: %s", error)


class HeaderLayout:
    """Keeps the app's buttons on the side away from the window controls.

    With macOS-style controls at the start, the narrow sidebar header cannot hold them,
    New Note and a centered title, so New Note joins the end buttons and the title,
    already shown by the window, makes way.
    """

    def __init__(
        self,
        window: Adw.ApplicationWindow,
        sidebar_header: Adw.HeaderBar,
        content_header: Adw.HeaderBar,
        new_note_button: Gtk.Button,
        compact_breakpoint: Adw.Breakpoint,
    ) -> None:
        self._window = window
        self._sidebar_header = sidebar_header
        self._content_header = content_header
        self._new_note_button = new_note_button
        self._compact_breakpoint = compact_breakpoint
        self._new_note_at_end = False
        self._settings = Gtk.Settings.get_default()
        self._handler = 0
        if self._settings is not None:
            self._handler = self._settings.connect(
                "notify::gtk-decoration-layout", self._on_decoration_layout_changed
            )
            window.connect("destroy", self._on_window_destroy)
        self.sync()

    def _on_window_destroy(self, _window: Gtk.Window) -> None:
        """GTK settings outlive the window, so their handler must not."""
        if self._settings is not None:
            self._settings.disconnect(self._handler)

    def _on_decoration_layout_changed(
        self, _settings: Gtk.Settings, _spec: GObject.ParamSpec
    ) -> None:
        self.sync()

    def sync(self) -> None:
        """Apply the current decoration layout and window width to both headers."""
        settings = self._settings
        layout = DecorationLayout.parse(
            (settings.props.gtk_decoration_layout or "") if settings is not None else ""
        )
        at_end = layout.controls_at_start
        if at_end != self._new_note_at_end:
            self._new_note_at_end = at_end
            self._sidebar_header.remove(self._new_note_button)
            if at_end:
                self._sidebar_header.pack_end(self._new_note_button)
            else:
                self._sidebar_header.pack_start(self._new_note_button)
        self._sidebar_header.set_show_title(not at_end)
        compact = self._window.get_current_breakpoint() is self._compact_breakpoint
        self._content_header.set_decoration_layout(layout.close_only() if compact else None)
