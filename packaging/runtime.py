"""Point native libraries at the resources shipped with the frozen application."""

import ctypes.util
import os
import sys
from pathlib import Path

root = Path(sys._MEIPASS)
share = root / "share"
os.environ["XDG_DATA_DIRS"] = str(share) + os.pathsep + os.environ.get("XDG_DATA_DIRS", "")
os.environ["GSETTINGS_SCHEMA_DIR"] = str(share / "glib-2.0" / "schemas")
os.environ["GIO_MODULE_DIR"] = str(root / "gio_modules")
if sys.platform != "darwin":
    os.environ["FONTCONFIG_PATH"] = str(root / "etc" / "fonts")
if sys.platform == "linux":
    if os.environ.get("FLATPAK_ID"):
        os.environ["ALSA_CONFIG_PATH"] = str(share / "alsa/alsa-flatpak.conf")
        os.environ["ALSA_PLUGIN_DIR"] = str(root / "alsa_modules")
    elif not Path("/usr/share/alsa/alsa.conf").is_file():
        os.environ.setdefault("ALSA_CONFIG_PATH", str(share / "alsa/alsa.conf"))
        os.environ.setdefault("ALSA_CONFIG_DIR", str(share / "alsa"))
        os.environ.setdefault("ALSA_PLUGIN_DIR", str(root / "alsa_modules"))

# sounddevice uses ctypes to discover PortAudio, which otherwise consults the host's
# ldconfig/Homebrew/MSYS2 installation instead of the libraries in the bundle.
original_find_library = ctypes.util.find_library


def find_library(name: str) -> str | None:
    """Prefer the bundled PortAudio even when the host has no audio development tools."""
    if "portaudio" in name:
        for pattern in ("libportaudio*.so*", "libportaudio*.dylib", "*portaudio*.dll"):
            if library := next(root.glob(pattern), None):
                return str(library)
    return original_find_library(name)


ctypes.util.find_library = find_library
