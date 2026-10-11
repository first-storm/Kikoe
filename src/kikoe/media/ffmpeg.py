"""The single gateway to the ffmpeg executable.

Lookup order: ``$KIKOE_FFMPEG``, a copy bundled next to a frozen (PyInstaller)
executable, then ``PATH``. Always call ffmpeg through :func:`run`.
"""

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_EXE_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


class FFmpegError(Exception):
    """ffmpeg is missing or exited with an error."""


def find_ffmpeg() -> Path:
    """Return the ffmpeg executable to use, or raise :class:`FFmpegError`."""
    if override := os.environ.get("KIKOE_FFMPEG"):
        path = Path(override)
        if path.is_file():
            return path
        msg = f"KIKOE_FFMPEG points to a missing file: {path}"
        raise FFmpegError(msg)

    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().parent / _EXE_NAME
        if bundled.is_file():
            return bundled

    if found := shutil.which("ffmpeg"):
        return Path(found)

    msg = "ffmpeg was not found. Install it or set KIKOE_FFMPEG."
    raise FFmpegError(msg)


async def run(*args: str) -> None:
    """Run ffmpeg with ``args`` (without the program name). Raise on failure."""
    exe = find_ffmpeg()
    cmd = [str(exe), "-hide_banner", "-nostdin", "-loglevel", "error", *args]
    log.debug("Running %s", cmd)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = stderr.decode(errors="replace").strip()
        msg = f"ffmpeg exited with status {proc.returncode}: {detail}"
        raise FFmpegError(msg)
