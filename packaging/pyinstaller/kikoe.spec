# PyInstaller build description for the AppImage, macOS and Windows packages.
#
# Run from the repository root, in an environment where the Kikoe wheel (with its
# compiled .ui and .mo files) and the locked runtime dependencies are installed:
#
#   KIKOE_FFMPEG_BIN=/path/to/ffmpeg [KIKOE_ICON=icon.png] \
#       pyinstaller --noconfirm packaging/pyinstaller/kikoe.spec
#
# The result is a one-folder app in dist/ (dist/Kikoe.app on macOS).
# ruff: noqa: F821 - Analysis, PYZ, EXE, COLLECT, BUNDLE and SPECPATH come from PyInstaller.

import os
import sys
from importlib.metadata import version
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

APP_ID = "io.github.first_storm.Kikoe"
VERSION = version("kikoe")
HERE = Path(SPECPATH)

ffmpeg = Path(os.environ["KIKOE_FFMPEG_BIN"]).resolve()
if not ffmpeg.is_file():
    raise SystemExit(f"KIKOE_FFMPEG_BIN does not point to a file: {ffmpeg}")
icon = os.environ.get("KIKOE_ICON")

# Lands in sys._MEIPASS, where kikoe.media.ffmpeg looks for it.
binaries = [(str(ffmpeg), ".")]
if sys.platform == "win32":
    # MSYS2's PortAudio; sounddevice's own hook only finds the pip wheel's copy.
    binaries += [(str(dll), ".") for dll in (Path(sys.base_prefix) / "bin").glob("libportaudio*.dll")]

datas = collect_data_files("kikoe", excludes=["**/*.blp"])
# Packages that read their own version (or entry points) from metadata at import time.
for distribution in (
    "kikoe",
    "keyring",
    "deepgram-sdk",
    "google-genai",
    "mistralai",
    "openai",
    "openrouter",
    "websockets",
):
    datas += copy_metadata(distribution)

# Providers and keyring backends are imported lazily or through entry points; the Mistral
# SDK resolves its models and resources on first use, so PyInstaller cannot see them.
hiddenimports = (
    collect_submodules("kikoe")
    + collect_submodules("keyring.backends")
    + collect_submodules("mistralai")
)

a = Analysis(
    [str(HERE / "launch.py")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hooksconfig={
        "gi": {
            "module-versions": {"Gtk": "4.0", "Gdk": "4.0", "Gsk": "4.0"},
            "icons": ["Adwaita", "hicolor"],
            "themes": [],
            "languages": ["zh_CN"],
        },
    },
    runtime_hooks=[str(HERE / "rthook_portaudio.py")],
    excludes=["tkinter", "pytest"],
)

if sys.platform.startswith("linux"):
    # The host's ALSA library must be used: a bundled copy cannot find the host's
    # plugins (PipeWire, PulseAudio) or configuration.
    a.binaries = [entry for entry in a.binaries if not entry[0].startswith("libasound.so")]

# Without PortAudio the app starts fine but cannot record or play, which the CI
# start-up check would not notice.
portaudio = [entry[0] for entry in a.binaries if Path(entry[0]).name.startswith("libportaudio")]
if not portaudio:
    raise SystemExit("PortAudio is missing from the bundle; recording would not work.")
print(f"Bundled PortAudio: {', '.join(portaudio)}")  # noqa: T201 - build log output

pyz = PYZ(a.pure)
# Linux libraries (libpython in particular) carry debug symbols. macOS binaries are
# left alone because stripping invalidates their signatures.
strip = sys.platform.startswith("linux")

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="kikoe" if sys.platform.startswith("linux") else "Kikoe",
    console=False,
    icon=icon,
    strip=strip,
    upx=False,
)

app = COLLECT(exe, a.binaries, a.datas, name="Kikoe", strip=strip, upx=False)

if sys.platform == "darwin":
    BUNDLE(
        app,
        name="Kikoe.app",
        icon=icon,
        bundle_identifier=APP_ID,
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "Kikoe",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSApplicationCategoryType": "public.app-category.productivity",
            "LSMinimumSystemVersion": os.environ.get("MACOSX_DEPLOYMENT_TARGET", "15.0"),
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "Kikoe records your microphone to turn speech into text notes."
            ),
        },
    )
