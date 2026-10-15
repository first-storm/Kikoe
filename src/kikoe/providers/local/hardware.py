"""What this computer is, as far as choosing an engine build is concerned.

Only cheap, portable facts are checked here. Whether a GPU really works is learnt by
starting the engine and asking it (the worker's ``probe``), which is the only reliable
test inside sandboxes and on every operating system.
"""

import os
import platform
import sys

from kikoe.core.local_models import Architecture, OperatingSystem, Platform

_ARCHITECTURES = {
    "x86_64": Architecture.X86_64,
    "amd64": Architecture.X86_64,
    "aarch64": Architecture.ARM64,
    "arm64": Architecture.ARM64,
}


def current_platform() -> Platform | None:
    """None on a system no engine build exists for."""
    if sys.platform.startswith("linux"):
        system = OperatingSystem.LINUX
    elif sys.platform == "darwin":
        system = OperatingSystem.MACOS
    elif sys.platform == "win32":
        system = OperatingSystem.WINDOWS
    else:
        return None
    arch = _ARCHITECTURES.get(platform.machine().lower())
    return Platform(system, arch) if arch else None


def in_flatpak() -> bool:
    """Flatpak hides NVIDIA devices unless the user grants ``--device=all``."""
    return "FLATPAK_ID" in os.environ
