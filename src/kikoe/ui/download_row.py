"""A row for something that can be downloaded, cancelled while downloading, and removed."""

from collections.abc import Callable

from gi.repository import Adw, GLib, Gtk

from kikoe.core.local_models import InstallState, InstallStatus
from kikoe.i18n import _
from kikoe.ui.templates import ui_file


def _nothing() -> None:
    pass


@Gtk.Template(filename=ui_file("download_row"))
class DownloadRow(Adw.ActionRow):
    __gtype_name__ = "KikoeDownloadRow"

    download_button: Gtk.Button = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    remove_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self, title: str) -> None:
        super().__init__(title=title)
        self.on_download: Callable[[], None] = _nothing
        self.on_cancel: Callable[[], None] = _nothing
        self.on_remove: Callable[[], None] = _nothing

    def show_state(self, state: InstallState, size: int, detail: str = "", note: str = "") -> None:
        """``detail`` describes the item; ``note`` explains why it cannot be used yet."""
        status = state.status
        self.download_button.set_visible(status is InstallStatus.MISSING)
        self.cancel_button.set_visible(status is InstallStatus.DOWNLOADING)
        self.remove_button.set_visible(status is InstallStatus.INSTALLED)
        self.set_subtitle(" · ".join(p for p in (_status_text(state, size), detail, note) if p))

    @Gtk.Template.Callback()
    def _on_download_button_clicked(self, _button: Gtk.Button) -> None:
        self.on_download()

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self.on_cancel()

    @Gtk.Template.Callback()
    def _on_remove_button_clicked(self, _button: Gtk.Button) -> None:
        self.on_remove()


def _status_text(state: InstallState, size: int) -> str:
    match state.status:
        case InstallStatus.MISSING:
            return GLib.format_size(size)
        case InstallStatus.DOWNLOADING if state.total:
            return _("Downloading… {received} of {total}").format(
                received=GLib.format_size(state.received), total=GLib.format_size(state.total)
            )
        case InstallStatus.DOWNLOADING:
            return _("Downloading…")
        case InstallStatus.INSTALLED:
            return _("Downloaded · {size}").format(size=GLib.format_size(size))
        case InstallStatus.BUNDLED:
            return _("Included with Kikoe")
