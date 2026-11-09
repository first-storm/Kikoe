"""Combine the microphone and system audio into the single stream a recording keeps."""

import asyncio
import contextlib
from collections.abc import AsyncIterator

from kikoe.core.pcm import Resampler, mix
from kikoe.media.recorder import Recorder

MAX_LAG_SECONDS = 0.5
"""System audio waiting beyond this is dropped, so clock drift cannot build up delay."""


class MixedRecorder:
    """The microphone's clock paces output; system audio fills in or is padded with silence.

    The two devices run on independent clocks. Following one avoids gaps in the voice
    track; a small lag cap absorbs the other's drift.
    """

    def __init__(self, microphone: Recorder, system: Recorder) -> None:
        self.sample_rate = 0
        self._microphone = microphone
        self._system = system
        self._pending = bytearray()
        self._resampler: Resampler | None = None
        self._system_error: BaseException | None = None

    async def start(self) -> None:
        # System audio first: on macOS it restarts PortAudio, which would close an open
        # microphone stream.
        await self._system.start()
        try:
            await self._microphone.start()
        except BaseException:
            await self._system.stop()
            raise
        self.sample_rate = self._microphone.sample_rate
        self._resampler = Resampler(self._system.sample_rate, self.sample_rate)

    async def _collect_system(self) -> None:
        resampler = self._resampler
        assert resampler is not None
        limit = round(self.sample_rate * MAX_LAG_SECONDS) * 2
        try:
            async for chunk in self._system.chunks():
                self._pending += resampler.process(chunk)
                if len(self._pending) > limit:
                    del self._pending[: len(self._pending) - limit]
        except Exception as error:
            self._system_error = error

    async def chunks(self) -> AsyncIterator[bytes]:
        task = asyncio.create_task(self._collect_system())
        first = True
        try:
            async for voice in self._microphone.chunks():
                if self._system_error is not None:
                    raise self._system_error
                if first:
                    # Audio the system produced while the microphone opened is stale.
                    del self._pending[: max(0, len(self._pending) - len(voice))]
                    first = False
                size = len(voice)
                other = bytes(self._pending[:size])
                del self._pending[:size]
                yield mix(voice, other.ljust(size, b"\x00"))
            await task
            if self._system_error is not None:
                raise self._system_error
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def pause(self) -> None:
        await asyncio.gather(self._microphone.pause(), self._system.pause())

    async def resume(self) -> None:
        await asyncio.gather(self._microphone.resume(), self._system.resume())
        self._pending.clear()

    async def stop(self) -> None:
        try:
            await self._microphone.stop()
        finally:
            await self._system.stop()
