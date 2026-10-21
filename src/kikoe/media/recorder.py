"""Capture the chosen microphone without involving GTK or blocking its main loop."""

import asyncio
import importlib
import queue
import sys
import threading
from array import array
from collections.abc import AsyncIterator, Buffer, Callable
from typing import Protocol, cast

from kikoe.i18n import _
from kikoe.media.devices import find_device


class RecorderError(Exception):
    """Microphone capture failed; already captured audio can still be saved."""


class Recorder(Protocol):
    """Capture mono little-endian int16 PCM at the input device's native sample rate."""

    sample_rate: int

    async def start(self) -> None: ...
    async def pause(self) -> None: ...
    async def resume(self) -> None: ...
    async def stop(self) -> None: ...
    def chunks(self) -> AsyncIterator[bytes]: ...


class _Status(Protocol):
    @property
    def input_overflow(self) -> bool: ...


class _InputStream(Protocol):
    @property
    def active(self) -> bool: ...
    def start(self) -> object: ...
    def stop(self) -> object: ...
    def close(self) -> object: ...


class _Backend(Protocol):
    """Typed boundary around sounddevice's untyped CFFI API."""

    def query_devices(self, device: int | None = None, kind: str | None = None) -> object: ...
    def RawInputStream(  # noqa: N802  # External API's spelling.
        self,
        *,
        device: int | None,
        samplerate: int,
        channels: int,
        dtype: str,
        callback: Callable[[Buffer, int, object, _Status], None],
        blocksize: int,
        latency: float,
    ) -> _InputStream: ...


class SoundDeviceRecorder:
    """Keep PortAudio callbacks short and bound the memory used by slow disk writers."""

    def __init__(self, device: str | None = None, backend: _Backend | None = None) -> None:
        self.sample_rate = 0
        self._device = device
        self._backend = backend
        self._stream: _InputStream | None = None
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=64)
        self._paused = threading.Event()
        self._stopped = threading.Event()
        self._error: RecorderError | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Open the chosen or default device; report missing libraries and permissions as errors."""
        async with self._lock:
            await asyncio.to_thread(self._open)

    def _open(self) -> None:
        try:
            backend = self._backend or cast(_Backend, importlib.import_module("sounddevice"))
            device = find_device("input", self._device, backend)
            info = cast(dict[str, object], backend.query_devices(device, "input"))
            rate = info["default_samplerate"]
            if not isinstance(rate, (int, float)) or rate <= 0:
                raise ValueError("No default input sample rate")
            self.sample_rate = round(rate)
            self._stream = backend.RawInputStream(
                device=device,
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                callback=self._on_audio,
                blocksize=max(1, self.sample_rate // 20),
                # Speech recording favors reliable capture over monitoring latency.
                latency=0.2,
            )
            self._stream.start()
        except Exception as error:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            raise RecorderError(
                _(
                    "Could not open the microphone. Check your input device and "
                    "microphone permissions. ({detail})"
                ).format(detail=error)
            ) from error

    def _on_audio(self, data: Buffer, _frames: int, _time: object, status: _Status) -> None:
        if self._stopped.is_set() or self._paused.is_set() or self._error is not None:
            return
        if status.input_overflow:
            self._error = RecorderError(
                _("The microphone lost audio. The captured audio has been kept.")
            )
            return
        chunk = bytes(data)
        if sys.byteorder != "little":
            samples = array("h", chunk)
            samples.byteswap()
            chunk = samples.tobytes()
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            self._error = RecorderError(
                _("Audio could not be saved fast enough. The captured audio has been kept.")
            )

    async def chunks(self) -> AsyncIterator[bytes]:
        """Drain accepted blocks before reporting a device error or a normal stop."""
        while True:
            chunk = await asyncio.to_thread(self._next_chunk)
            if chunk is None:
                if self._error is not None:
                    raise self._error
                return
            yield chunk

    def _next_chunk(self) -> bytes | None:
        while True:
            try:
                return self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._error is not None or self._stopped.is_set():
                    return None
                stream = self._stream
                if stream is not None and not self._paused.is_set() and not stream.active:
                    self._error = RecorderError(
                        _("The microphone disconnected. The captured audio has been kept.")
                    )
                    return None

    async def pause(self) -> None:
        """Stop the device so the next resume cannot include buffered pause audio."""
        async with self._lock:
            self._paused.set()
            await self._control("pause")

    async def resume(self) -> None:
        async with self._lock:
            if self._stopped.is_set():
                return
            await self._control("resume")
            # Disconnect polling must ignore the inactive device until startup finishes.
            self._paused.clear()

    async def _control(self, operation: str) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            await asyncio.to_thread(stream.start if operation == "resume" else stream.stop)
        except Exception as error:
            self._error = RecorderError(
                _("The microphone stopped responding. ({detail})").format(detail=error)
            )
            raise self._error from error

    async def stop(self) -> None:
        """Close once; a waiting consumer wakes even when no audio has arrived."""
        async with self._lock:
            if self._stopped.is_set():
                return
            self._stopped.set()
            stream, self._stream = self._stream, None
            if stream is not None:
                try:
                    try:
                        await asyncio.to_thread(stream.stop)
                    finally:
                        await asyncio.to_thread(stream.close)
                except Exception as error:
                    self._error = RecorderError(
                        _("Could not close the microphone. ({detail})").format(detail=error)
                    )
                    raise self._error from error
