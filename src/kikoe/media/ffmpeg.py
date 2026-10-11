"""The single gateway to the ffmpeg executable.

Lookup order: ``$KIKOE_FFMPEG``, a copy bundled next to a frozen (PyInstaller)
executable, then ``PATH``. Always call ffmpeg through :func:`run`.
"""

import asyncio
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_EXE_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


class FFmpegError(Exception):
    """ffmpeg is missing or exited with an error."""


class FFmpegNotFoundError(FFmpegError):
    """No usable ffmpeg executable; nothing about the input file is known yet."""


def find_ffmpeg() -> Path:
    """Return the ffmpeg executable to use, or raise :class:`FFmpegError`."""
    if override := os.environ.get("KIKOE_FFMPEG"):
        path = Path(override)
        if path.is_file():
            return path
        msg = f"KIKOE_FFMPEG points to a missing file: {path}"
        raise FFmpegNotFoundError(msg)

    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).resolve().parent / _EXE_NAME
        if bundled.is_file():
            return bundled

    if found := shutil.which("ffmpeg"):
        return Path(found)

    msg = "ffmpeg was not found. Install it or set KIKOE_FFMPEG."
    raise FFmpegNotFoundError(msg)


@dataclass(frozen=True, slots=True)
class FFmpegOutput:
    stdout: str
    """E.g. ``-progress pipe:1`` reports."""
    stderr: str
    """Log lines at the requested level, e.g. filter results such as ``silencedetect``."""


async def run(*args: str, loglevel: str = "error") -> FFmpegOutput:
    """Run ffmpeg with ``args`` (without the program name). Raise on failure."""
    exe = find_ffmpeg()
    cmd = [str(exe), "-hide_banner", "-nostdin", "-loglevel", loglevel, *args]
    log.debug("Running %s", cmd)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        # Don't leave a long conversion running after the user cancelled.
        proc.kill()
        await proc.wait()
        raise
    output = FFmpegOutput(stdout.decode(errors="replace"), stderr.decode(errors="replace"))
    if proc.returncode != 0:
        msg = f"ffmpeg exited with status {proc.returncode}: {output.stderr.strip()}"
        raise FFmpegError(msg)
    return output
