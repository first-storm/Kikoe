"""Record what Linux plays through the default output's PulseAudio monitor.

PipeWire desktops serve the same API through pipewire-pulse, and Flatpak's PulseAudio
socket already grants it, so no sound-server tools or extra permissions are needed.
The library is loaded with ctypes only when system audio is requested.
"""

import asyncio
import ctypes
import logging
import threading
from collections.abc import AsyncIterator
from functools import cache
from typing import Protocol

from kikoe.i18n import _
from kikoe.media.recorder import ChunkQueue, SystemAudioError

log = logging.getLogger(__name__)

_LIBRARY = "libpulse-simple.so.0"
MONITOR_DEVICE = "@DEFAULT_MONITOR@"
"""Follows the desktop's current output, so headphones and speakers both work."""
SAMPLE_RATE = 48000
_BLOCK_BYTES = SAMPLE_RATE // 20 * 2
_SAMPLE_S16LE = 3
_STREAM_RECORD = 2
_DEFAULT = 0xFFFFFFFF


class _SampleSpec(ctypes.Structure):
    _fields_ = (  # ctypes reads this class attribute.
        ("format", ctypes.c_int),
        ("rate", ctypes.c_uint32),
        ("channels", ctypes.c_uint8),
    )


class _BufferAttributes(ctypes.Structure):
    _fields_ = (  # ctypes reads this class attribute.
        ("maxlength", ctypes.c_uint32),
        ("tlength", ctypes.c_uint32),
        ("prebuf", ctypes.c_uint32),
        ("minreq", ctypes.c_uint32),
        ("fragsize", ctypes.c_uint32),
    )


class PulseStream(Protocol):
    """One blocking record stream; reads and close happen on the reader thread."""

    def read(self, size: int) -> bytes: ...
    def close(self) -> None: ...


class PulseLibrary(Protocol):
    def open_monitor(self, rate: int) -> PulseStream: ...


class _PulseError(Exception):
    pass


class _CtypesStream:
    def __init__(self, library: ctypes.CDLL, handle: int) -> None:
        self._library = library
        self._handle = handle

    def read(self, size: int) -> bytes:
        data = ctypes.create_string_buffer(size)
        error = ctypes.c_int(0)
        if self._library.pa_simple_read(self._handle, data, size, ctypes.byref(error)) < 0:
            raise _PulseError(_describe(self._library, error.value))
        return data.raw

    def close(self) -> None:
        self._library.pa_simple_free(self._handle)


class _CtypesPulse:
    def __init__(self, library: ctypes.CDLL) -> None:
        library.pa_simple_new.restype = ctypes.c_void_p
        library.pa_simple_new.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.POINTER(_SampleSpec),
            ctypes.c_void_p,
            ctypes.POINTER(_BufferAttributes),
            ctypes.POINTER(ctypes.c_int),
        )
        library.pa_simple_read.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_int),
        )
        library.pa_simple_free.argtypes = (ctypes.c_void_p,)
        library.pa_strerror.restype = ctypes.c_char_p
        library.pa_strerror.argtypes = (ctypes.c_int,)
        self._library = library

    def open_monitor(self, rate: int) -> PulseStream:
        spec = _SampleSpec(_SAMPLE_S16LE, rate, 1)
        # A small fragment keeps the level meter and live transcription responsive.
        attributes = _BufferAttributes(_DEFAULT, _DEFAULT, _DEFAULT, _DEFAULT, _BLOCK_BYTES)
        error = ctypes.c_int(0)
        handle: int | None = self._library.pa_simple_new(
            None,
            b"Kikoe",
            _STREAM_RECORD,
            MONITOR_DEVICE.encode(),
            b"System audio",
            ctypes.byref(spec),
            None,
            ctypes.byref(attributes),
            ctypes.byref(error),
        )
        if not handle:
            raise _PulseError(_describe(self._library, error.value))
        return _CtypesStream(self._library, handle)


def _describe(library: ctypes.CDLL, code: int) -> str:
    message: bytes | None = library.pa_strerror(code)
    return message.decode(errors="replace") if message else f"error {code}"


@cache
def load_library() -> PulseLibrary | None:
    """The sound server's client library, or None where it is not installed."""
    try:
        return _CtypesPulse(ctypes.CDLL(_LIBRARY))
    except (OSError, AttributeError) as error:
        log.info("System audio is unavailable: %s", error)
        return None


class PulseSystemRecorder:
    """Read the monitor on a thread; pausing discards blocks so paused audio is never kept."""

    def __init__(self, library: PulseLibrary | None = None) -> None:
        self.sample_rate = SAMPLE_RATE
        self._library = library
        self._stream: PulseStream | None = None
        self._buffer = ChunkQueue()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        library = self._library or load_library()
        if library is None:
            raise SystemAudioError(
                _("System audio needs PulseAudio or PipeWire, which was not found.")
            )
        try:
            self._stream = await asyncio.to_thread(library.open_monitor, self.sample_rate)
        except _PulseError as error:
            raise SystemAudioError(
                _("Could not record system audio. ({detail})").format(detail=error)
            ) from error
        self._thread = threading.Thread(target=self._read, name="kikoe-system-audio", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        stream = self._stream
        assert stream is not None
        try:
            while not self._buffer.stopped.is_set():
                chunk = stream.read(_BLOCK_BYTES)
                if not self._paused.is_set() and not self._buffer.stopped.is_set():
                    self._buffer.put(chunk)
                if self._buffer.error is not None:
                    return
        except _PulseError as error:
            self._buffer.fail(
                SystemAudioError(
                    _("System audio stopped. The captured audio has been kept. ({detail})").format(
                        detail=error
                    )
                )
            )
        finally:
            stream.close()

    def chunks(self) -> AsyncIterator[bytes]:
        return self._buffer.drain()

    async def pause(self) -> None:
        self._paused.set()

    async def resume(self) -> None:
        self._paused.clear()

    async def stop(self) -> None:
        """The reader closes the stream itself, because freeing it mid-read would crash."""
        self._buffer.stopped.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            await asyncio.to_thread(thread.join)
