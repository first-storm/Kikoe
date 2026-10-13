"""The single gateway to the ffmpeg executable.

Lookup order: ``$KIKOE_FFMPEG``, a copy bundled in a frozen (PyInstaller) build,
then ``PATH``. Always call ffmpeg through :func:`run`.
"""

import asyncio
import logging
import os
import shutil
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from kikoe.i18n import _

log = logging.getLogger(__name__)

_EXE_NAME = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

_CREATE_NO_WINDOW = 0x08000000
"""``subprocess.CREATE_NO_WINDOW``; the name only exists on Windows."""


class FFmpegError(Exception):
    """ffmpeg is missing or exited with an error."""


class FFmpegNotFoundError(FFmpegError):
    """No usable ffmpeg executable; nothing about the input file is known yet."""


def _bundle_dirs() -> list[Path]:
    """Directories a frozen build may keep ffmpeg in.

    PyInstaller 6 puts collected binaries in ``sys._MEIPASS`` (``_internal`` or, in a
    macOS app, ``Contents/Frameworks``), not next to the executable.
    """
    if not getattr(sys, "frozen", False):
        return []
    dirs = [Path(sys.executable).resolve().parent]
    if meipass := getattr(sys, "_MEIPASS", None):
        dirs.insert(0, Path(meipass))
    return dirs


def find_ffmpeg() -> Path:
    """Return the ffmpeg executable to use, or raise :class:`FFmpegError`."""
    if override := os.environ.get("KIKOE_FFMPEG"):
        path = Path(override)
        if path.is_file():
            return path
        msg = _("KIKOE_FFMPEG points to a missing file: {path}").format(path=path)
        raise FFmpegNotFoundError(msg)

    for directory in _bundle_dirs():
        bundled = directory / _EXE_NAME
        if bundled.is_file():
            return bundled

    if found := shutil.which("ffmpeg"):
        return Path(found)

    msg = _("ffmpeg was not found. Install it or set KIKOE_FFMPEG.")
    raise FFmpegNotFoundError(msg)


def _creation_flags() -> int:
    """Stop Windows opening a console window for ffmpeg from the windowed build."""
    return _CREATE_NO_WINDOW if sys.platform == "win32" else 0


async def _spawn(*args: str, stdin: int | None = None) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *args,
        stdin=stdin,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_creation_flags(),
    )


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
    proc = await _spawn(*cmd)
    try:
        stdout, stderr = await proc.communicate()
    except asyncio.CancelledError:
        # Don't leave a long conversion running after the user cancelled.
        proc.kill()
        await proc.wait()
        raise
    output = FFmpegOutput(stdout.decode(errors="replace"), stderr.decode(errors="replace"))
    if proc.returncode != 0:
        msg = _("ffmpeg exited with status {status}: {error}").format(
            status=proc.returncode, error=output.stderr.strip()
        )
        raise FFmpegError(msg)
    return output


class PCMStream:
    """Resample bounded PCM buffers while keeping native audio in the durable journal."""

    def __init__(self, process: asyncio.subprocess.Process, output_rate: int) -> None:
        self._process = process
        self._rate = output_rate
        self._errors = asyncio.create_task(self._read_errors())

    @classmethod
    async def start(cls, input_rate: int, output_rate: int) -> PCMStream:
        executable = await asyncio.to_thread(find_ffmpeg)
        process = await _spawn(
            str(executable),
            "-hide_banner",
            "-loglevel",
            "error",
            "-probesize",
            "32",
            "-analyzeduration",
            "0",
            "-f",
            "s16le",
            "-ar",
            str(input_rate),
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-ar",
            str(output_rate),
            "-ac",
            "1",
            "-f",
            "s16le",
            "-flush_packets",
            "1",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
        )
        return cls(process, output_rate)

    async def feed(self, pcm: bytes) -> None:
        stdin = self._process.stdin
        if stdin is None:
            raise FFmpegError(_("The audio converter has no input stream."))
        try:
            stdin.write(pcm)
            await stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as error:
            raise FFmpegError(_("The audio converter stopped unexpectedly.")) from error

    async def finish(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()

    async def chunks(self) -> AsyncIterator[bytes]:
        stdout = self._process.stdout
        if stdout is None:
            raise FFmpegError(_("The audio converter has no output stream."))
        while True:
            try:
                yield await stdout.readexactly(self._rate // 10 * 2)
            except asyncio.IncompleteReadError as error:
                if error.partial:
                    yield error.partial
                break
        code = await self._process.wait()
        detail = await self._errors
        if code:
            raise FFmpegError(_("The audio converter failed: {detail}").format(detail=detail))

    async def close(self) -> None:
        if self._process.returncode is None:
            self._process.kill()
        await self._process.wait()
        await self._errors

    async def _read_errors(self) -> str:
        stderr = self._process.stderr
        if stderr is None:
            return ""
        tail = b""
        while chunk := await stderr.read(4096):
            tail = (tail + chunk)[-4096:]
        return tail.decode(errors="replace")


async def decode_audio(
    source: Path, sample_rate: int, start: float = 0, end: float | None = None
) -> AsyncGenerator[bytes]:
    """Stream bounded playback buffers rather than loading a whole recording."""
    executable = await asyncio.to_thread(find_ffmpeg)
    args = [
        str(executable),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-ss",
        str(start),
        "-i",
        str(source),
    ]
    if end is not None:
        args.extend(["-t", str(end - start)])
    args.extend(["-vn", "-ar", str(sample_rate), "-ac", "1", "-f", "s16le", "pipe:1"])
    process = await _spawn(*args)

    async def read_errors() -> bytes:
        tail = b""
        if process.stderr is not None:
            while chunk := await process.stderr.read(4096):
                tail = (tail + chunk)[-8192:]
        return tail

    errors = asyncio.create_task(read_errors())
    try:
        assert process.stdout is not None
        while True:
            try:
                yield await process.stdout.readexactly(max(2, sample_rate // 20 * 2))
            except asyncio.IncompleteReadError as error:
                if error.partial:
                    yield error.partial
                break
        code = await process.wait()
        detail = (await errors).decode(errors="replace")
        if code:
            raise FFmpegError(_("Could not decode the audio: {detail}").format(detail=detail))
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await errors
