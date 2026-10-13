"""PyInstaller runtime hook: let sounddevice find the bundled PortAudio library.

sounddevice looks PortAudio up with ``ctypes.util.find_library("portaudio")``. On Linux
that only searches system paths, and on Windows (MSYS2) the library is called
``libportaudio.dll``, which the search does not match. Both would miss the copy that
the spec bundles next to the executable.
"""

import ctypes.util
import sys
from pathlib import Path

_bundle = Path(getattr(sys, "_MEIPASS", ""))
_bundled = sorted(
    path
    for pattern in ("libportaudio*.so*", "libportaudio*.dll", "libportaudio*.dylib")
    for path in _bundle.glob(pattern)
)

if _bundled:
    _find_library = ctypes.util.find_library

    def _find_bundled_library(name: str) -> str | None:
        if name == "portaudio":
            return str(_bundled[0])
        return _find_library(name)

    ctypes.util.find_library = _find_bundled_library
