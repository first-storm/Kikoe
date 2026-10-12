# Build with `uv run --group packaging scripts/package.py freeze`.
import os
import sys
from pathlib import Path

from PyInstaller.depend.bindepend import resolve_library_path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
prefix = Path(os.environ["KIKOE_NATIVE_PREFIX"])
ffmpeg = Path(os.environ["KIKOE_BUILD_FFMPEG"])
datas = collect_data_files("kikoe") + copy_metadata("kikoe", recursive=True)
datas += [(str(root / "LICENSE"), "licenses/kikoe")]
datas += [(str(root / "build/build-info.json"), ".")]
binaries = [(str(ffmpeg), ".")]

for library in ("libportaudio.so.2", "libportaudio.dylib", "libportaudio-2.dll"):
    resolved = resolve_library_path(library)
    if resolved:
        binaries.append((resolved, "."))
        break
else:
    raise RuntimeError("PortAudio was not found; install it in KIKOE_NATIVE_PREFIX")

# GIO loads these with dlopen; they do not appear in GTK's link dependencies.
for folder in prefix.glob("lib/**/gio/modules"):
    for module in folder.glob("*"):
        if module.suffix in (".so", ".dll", ".dylib"):
            binaries.append((str(module), "gio_modules"))

if sys.platform == "linux":
    for folder in prefix.glob("lib/**/alsa-lib"):
        for module in folder.glob("*.so"):
            binaries.append((str(module), "alsa_modules"))
    datas.append((str(root / "packaging/linux/alsa-flatpak.conf"), "share/alsa"))

for relative in ("share/glib-2.0/schemas", "etc/fonts", "share/fontconfig", "share/licenses", "share/alsa"):
    folder = prefix / relative
    if folder.is_dir():
        datas.append((str(folder), relative))

# Debian/Ubuntu native packages store their redistribution notices here.
for copyright_file in (prefix / "share/doc").glob("*/copyright"):
    datas.append((str(copyright_file), "licenses/native/" + copyright_file.parent.name))

a = Analysis(
    [str(root / "packaging/launcher.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("kikoe") + collect_submodules("keyring.backends")
        + ["gi.repository.Adw", "gi.repository.Gtk", "gi.repository.Gdk", "gi.repository.GIRepository"],
    hookspath=[str(root / "packaging/hooks")],
    hooksconfig={"gi": {"module-versions": {"Gtk": "4.0", "Gdk": "4.0", "Adw": "1", "GIRepository": "3.0"},
                        "icons": ["Adwaita", "hicolor"], "themes": [], "languages": ["en", "zh_CN"]}},
    runtime_hooks=[str(root / "packaging/runtime.py")],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="kikoe",
    debug=False, strip=False, upx=False, console=sys.platform == "linux",
    icon=os.environ.get("KIKOE_WINDOWS_ICON"),
    target_arch=os.environ.get("KIKOE_MACOS_ARCH"),
    codesign_identity=os.environ.get("KIKOE_CODESIGN_IDENTITY"),
    entitlements_file=str(root / "packaging/macos/entitlements.plist") if sys.platform == "darwin" else None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="kikoe")
if sys.platform == "darwin":
    app = BUNDLE(
        collection, name="Kikoe.app", bundle_identifier=os.environ["KIKOE_APP_ID"],
        icon=os.environ.get("KIKOE_MACOS_ICON"),
        info_plist={
            "CFBundleShortVersionString": os.environ["KIKOE_VERSION"],
            "CFBundleVersion": os.environ["KIKOE_VERSION"],
            "NSMicrophoneUsageDescription": "Kikoe records your microphone to turn speech into text notes.",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "14.0",
        },
    )
