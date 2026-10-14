"""Play retained audio on the chosen output device with bounded memory."""

import asyncio
import importlib
import sys
from array import array
from collections.abc import Callable
from contextlib import aclosing
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Protocol, cast

from kikoe.i18n import _
from kikoe.media.devices import find_device
from kikoe.media.ffmpeg import decode_audio


class PlaybackError(Exception):
    """Audio playback failed without changing the recording."""


class OutputStream(Protocol):
    """The small sounddevice interface playback needs."""

    def start(self) -> object: ...
    def write(self, data: bytes) -> bool: ...
    def stop(self) -> object: ...
    def abort(self) -> object: ...
    def close(self) -> object: ...


class OutputBackend(Protocol):
    """Typed boundary for PortAudio and a fake test output device."""

    def query_devices(self, device: int | None = None, kind: str | None = None) -> object: ...
    def RawOutputStream(  # noqa: N802  # External API's spelling.
        self, *, device: int | None, samplerate: int, channels: int, dtype: str
    ) -> OutputStream: ...


async def _device_call[T](operation: Callable[[], T]) -> T:
    """Wait for an in-flight device call before releasing its stream on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


async def play_audio(
    source: Path,
    *,
    start: timedelta = timedelta(),
    end: timedelta | None = None,
    device: str | None = None,
    backend: OutputBackend | None = None,
) -> None:
    """Play one range; cancelling closes both the decoder and the output device."""
    stream: OutputStream | None = None
    finished = False
    try:
        output = backend or cast(
            OutputBackend, await asyncio.to_thread(importlib.import_module, "sounddevice")
        )
        index = await _device_call(lambda: find_device("output", device, output))
        info = cast(
            dict[str, object], await _device_call(lambda: output.query_devices(index, "output"))
        )
        rate = info.get("default_samplerate")
        if not isinstance(rate, (int, float)) or rate <= 0:
            raise PlaybackError(_("No default audio output device is available."))
        opening = asyncio.create_task(
            asyncio.to_thread(
                output.RawOutputStream,
                device=index,
                samplerate=round(rate),
                channels=1,
                dtype="int16",
            )
        )
        try:
            stream = await asyncio.shield(opening)
        except asyncio.CancelledError:
            stream = await opening
            raise
        assert stream is not None
        await _device_call(stream.start)
        async with aclosing(
            decode_audio(
                source,
                round(rate),
                start.total_seconds(),
                end.total_seconds() if end is not None else None,
            )
        ) as chunks:
            async for chunk in chunks:
                data = chunk
                if sys.byteorder != "little":
                    samples = array("h", chunk)
                    samples.byteswap()
                    data = samples.tobytes()
                await _device_call(partial(stream.write, data))
        finished = True
    except Exception as error:
        raise PlaybackError(
            _("Could not play the audio. Check your output device. ({detail})").format(detail=error)
        ) from error
    finally:
        if stream is not None:
            try:
                try:
                    await _device_call(stream.stop if finished else stream.abort)
                finally:
                    await _device_call(stream.close)
            except Exception as error:
                raise PlaybackError(
                    _("Could not close the audio output device. ({detail})").format(detail=error)
                ) from error
