"""An engine's own settings on its page: download state, acceleration and device."""

from gi.repository import Adw, GLib, GObject, Gtk

from kikoe.constants import APP_ID
from kikoe.core.local_models import (
    Accelerator,
    EngineBuild,
    EngineDevice,
    EngineProbe,
    EngineSpec,
    InstallState,
    InstallStatus,
    needs_device_access,
)
from kikoe.i18n import _, ngettext
from kikoe.providers.base import TranscriptionError
from kikoe.services.local_models import LocalModelManager
from kikoe.ui.download_row import show_progress
from kikoe.ui.guard import Guard
from kikoe.ui.tasks import run_task
from kikoe.ui.templates import ui_file


@Gtk.Template(filename=ui_file("local_engine_group"))
class LocalEngineGroup(Adw.PreferencesGroup):
    __gtype_name__ = "KikoeLocalEngineGroup"

    download_button: Gtk.Button = Gtk.Template.Child()
    progress_bar: Gtk.ProgressBar = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    build_row: Adw.ComboRow = Gtk.Template.Child()
    device_row: Adw.ComboRow = Gtk.Template.Child()
    hint_label: Gtk.Label = Gtk.Template.Child()

    def __init__(self, manager: LocalModelManager, engine: EngineSpec) -> None:
        super().__init__()
        self._manager = manager
        self._engine = engine
        self._updating = Guard()
        self._shown_installed: EngineBuild | None = None
        self._devices: tuple[str | None, ...] = ()
        """Device IDs behind the device row, after "Automatic"."""
        self._builds = manager.builds(engine)
        self.build_row.set_model(Gtk.StringList.new([build_label(b) for b in self._builds]))
        recommended = manager.recommended_build(engine)
        if recommended is not None:
            with self._updating:
                self.build_row.set_selected(self._builds.index(recommended))
        self.refresh()

    @property
    def selected_build(self) -> EngineBuild:
        return self._builds[min(self.build_row.get_selected(), len(self._builds) - 1)]

    def refresh(self) -> None:
        engine_id = self._engine.engine_id
        state = self._manager.engine_state(engine_id)
        status = state.status
        installed = self._manager.installed_build(engine_id)
        if installed != self._shown_installed:
            # Follow a new install, but keep a choice the user is still making.
            self._shown_installed = installed
            if installed is not None and installed in self._builds:
                with self._updating:
                    self.build_row.set_selected(self._builds.index(installed))
        bundled = status is InstallStatus.BUNDLED
        switching = not bundled and installed is not None and self.selected_build != installed
        self.download_button.set_visible(status is InstallStatus.MISSING or switching)
        self.download_button.set_label(_("_Switch") if switching else _("_Download"))
        self.cancel_button.set_visible(status is InstallStatus.DOWNLOADING)
        self.build_row.set_sensitive(
            status not in (InstallStatus.DOWNLOADING, InstallStatus.BUNDLED)
        )
        size = sum(d.size for d in self._engine.downloads(self.selected_build))
        show_progress(self.progress_bar, InstallState(status, state.received, state.total or size))
        match status:
            case InstallStatus.MISSING:
                text = _("Not downloaded · {size}").format(size=GLib.format_size(size))
            case InstallStatus.DOWNLOADING:
                text = progress_text(state, size)
            case InstallStatus.INSTALLED | InstallStatus.BUNDLED if switching:
                text = _("Switching downloads {size}").format(size=GLib.format_size(size))
            case InstallStatus.INSTALLED | InstallStatus.BUNDLED:
                text = ready_text(self._manager, self._engine)
        self.set_description(text)
        self._show_devices(status in (InstallStatus.INSTALLED, InstallStatus.BUNDLED))
        self._show_hint()

    def _show_devices(self, installed: bool) -> None:
        engine_id = self._engine.engine_id
        probe = self._manager.probe_result(engine_id)
        if installed and probe is None:
            run_task(self._manager.probe(engine_id), self)
        if not installed or not isinstance(probe, EngineProbe):
            self.device_row.set_visible(False)
            return
        with self._updating:
            self._devices = (None, *(d.device_id for d in probe.devices))
            names = [_("Automatic"), *(device_label(d) for d in probe.devices)]
            self.device_row.set_model(Gtk.StringList.new(names))
            saved = self._manager.device(engine_id)
            self.device_row.set_selected(
                self._devices.index(saved) if saved in self._devices else 0
            )
        self.device_row.set_visible(True)

    def _show_hint(self) -> None:
        build = self.selected_build
        hint = ""
        probe = self._manager.probe_result(self._engine.engine_id)
        installed = self._manager.installed_build(self._engine.engine_id)
        if isinstance(probe, TranscriptionError) and build == installed:
            hint = str(probe)
        elif needs_device_access(build, self._manager.sandboxed):
            hint = _(
                "CUDA needs access to the NVIDIA devices, which Flatpak hides by default. "
                "To allow it, run this command and restart Kikoe:\n{command}"
            ).format(command=f"flatpak override --user --device=all {APP_ID}")
        elif Accelerator.CUDA in build.accelerators:
            hint = _("Needs an NVIDIA graphics card with a current driver.")
        self.hint_label.set_label(hint)
        self.hint_label.set_visible(bool(hint))

    @Gtk.Template.Callback()
    def _on_download_button_clicked(self, _button: Gtk.Button) -> None:
        build = self.selected_build
        run_task(self._manager.install_engine(self._engine.engine_id, build.build_id), self)

    @Gtk.Template.Callback()
    def _on_cancel_button_clicked(self, _button: Gtk.Button) -> None:
        self._manager.cancel(self._engine.engine_id)

    @Gtk.Template.Callback()
    def _on_build_row_notify_selected(self, _row: Adw.ComboRow, _spec: GObject.ParamSpec) -> None:
        if not self._updating.active:
            self.refresh()

    @Gtk.Template.Callback()
    def _on_device_row_notify_selected(self, row: Adw.ComboRow, _spec: GObject.ParamSpec) -> None:
        index = row.get_selected()
        if self._updating.active or index >= len(self._devices):
            return
        run_task(self._manager.set_device(self._engine.engine_id, self._devices[index]), self)


def ready_text(manager: LocalModelManager, engine: EngineSpec) -> str:
    """E.g. “Version 1.2 · Ready”, or why an installed engine cannot be used yet."""
    version = _("Version {version}").format(version=engine.version)
    if manager.engine_state(engine.engine_id).status is InstallStatus.BUNDLED:
        version = _("Included with Kikoe · {version}").format(version=version)
    probe = manager.probe_result(engine.engine_id)
    if probe is None:
        return _("{version} · Starting…").format(version=version)
    if isinstance(probe, TranscriptionError):
        return _("{version} · Could not start").format(version=version)
    return _("{version} · Ready").format(version=version)


def progress_text(state: InstallState, size: int) -> str:
    return _("Downloading… {received} of {total}").format(
        received=GLib.format_size(state.received),
        total=GLib.format_size(state.total or size),
    )


def engine_summary(manager: LocalModelManager, engine: EngineSpec) -> str:
    """One line for the engine's row on the overview page."""
    state = manager.engine_state(engine.engine_id)
    build = manager.installed_build(engine.engine_id) or manager.recommended_build(engine)
    size = sum(d.size for d in engine.downloads(build)) if build else 0
    if state.status is InstallStatus.MISSING:
        return _("Not downloaded · {size}").format(size=GLib.format_size(size))
    if state.status is InstallStatus.DOWNLOADING:
        return progress_text(state, size)
    status = ready_text(manager, engine)
    count = len(manager.installed_models(engine.engine_id))
    if not count:
        return status
    return ngettext("{status} · {count} model", "{status} · {count} models", count).format(
        status=status, count=count
    )


_DEVICE_KINDS = {"vulkan": "Vulkan", "cuda": "CUDA", "metal": "Metal", "rocm": "ROCm"}


def device_label(device: EngineDevice) -> str:
    """E.g. “AMD Radeon 880M Graphics (Vulkan)” or “Processor (AMD Ryzen …)”."""
    if device.kind == "cpu":
        return _("Processor ({name})").format(name=device.name)
    return f"{device.name} ({_DEVICE_KINDS.get(device.kind, device.kind)})"


def build_label(build: EngineBuild) -> str:
    """Named after the fastest hardware the build can use."""
    if Accelerator.CUDA in build.accelerators:
        return _("CUDA (NVIDIA)")
    if Accelerator.METAL in build.accelerators:
        return _("Metal (Apple GPU)")
    if Accelerator.VULKAN in build.accelerators:
        return _("Vulkan (Most GPUs)")
    return _("CPU Only")
