"""One engine on the Local Models page: download state, acceleration and device."""

from gi.repository import Adw, GLib, GObject, Gtk

from kikoe.core.local_models import Accelerator, EngineBuild, EngineSpec, InstallStatus
from kikoe.i18n import _
from kikoe.services.local_models import LocalModelManager
from kikoe.ui.guard import Guard
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("local_engine_group"))
class LocalEngineGroup(Adw.PreferencesGroup):
    __gtype_name__ = "KikoeLocalEngineGroup"

    download_button: Gtk.Button = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    remove_button: Gtk.Button = Gtk.Template.Child()
    build_row: Adw.ComboRow = Gtk.Template.Child()
    device_row: Adw.ComboRow = Gtk.Template.Child()
    hint_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self, manager: LocalModelManager, engine: EngineSpec) -> None:
        super().__init__(title=engine.display_name)
        self._manager = manager
        self._engine = engine
        self._updating = Guard()
        assert manager.platform is not None
        self._builds = engine.builds_for(manager.platform)
        self.build_row.set_model(Gtk.StringList.new([build_label(b) for b in self._builds]))
        self.refresh()

    @property
    def selected_build(self) -> EngineBuild:
        return self._builds[min(self.build_row.get_selected(), len(self._builds) - 1)]

    def refresh(self) -> None:
        state = self._manager.engine_state(self._engine.engine_id)
        status = state.status
        self.download_button.set_visible(status is InstallStatus.MISSING)
        self.cancel_button.set_visible(status is InstallStatus.DOWNLOADING)
        self.remove_button.set_visible(status is InstallStatus.INSTALLED)
        size = sum(d.size for d in self._engine.downloads(self.selected_build))
        match status:
            case InstallStatus.MISSING:
                text = _("Not downloaded · {size}").format(size=GLib.format_size(size))
            case InstallStatus.DOWNLOADING:
                text = _("Downloading… {received} of {total}").format(
                    received=GLib.format_size(state.received),
                    total=GLib.format_size(state.total or size),
                )
            case InstallStatus.INSTALLED | InstallStatus.BUNDLED:
                text = _("Version {version}").format(version=self._engine.version)
        self.set_description(text)

    @Gtk.Template.Callback()
    def _on_download_button_clicked(self, _button: Gtk.Button) -> None:
        pass

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        pass

    @Gtk.Template.Callback()
    def _on_remove_button_clicked(self, _button: Gtk.Button) -> None:
        pass

    @Gtk.Template.Callback()
    def _on_build_row_notify_selected(self, _row: Adw.ComboRow, _spec: GObject.ParamSpec) -> None:
        if not self._updating.active:
            self.refresh()

    @Gtk.Template.Callback()
    def _on_device_row_notify_selected(self, _row: Adw.ComboRow, _spec: GObject.ParamSpec) -> None:
        pass


def build_label(build: EngineBuild) -> str:
    """Named after the fastest hardware the build can use."""
    if Accelerator.CUDA in build.accelerators:
        return _("CUDA (NVIDIA)")
    if Accelerator.METAL in build.accelerators:
        return _("Metal (Apple GPU)")
    if Accelerator.VULKAN in build.accelerators:
        return _("Vulkan (Most GPUs)")
    return _("CPU Only")
