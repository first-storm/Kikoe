"""Linux system audio through a fake PulseAudio library, never a real sound server."""

import asyncio
import threading

import pytest

from kikoe.media.recorder import SystemAudioError
from kikoe.media.system_audio_pulse import SAMPLE_RATE, PulseSystemRecorder, _PulseError
from tests.helpers import run_async


class FakeStream:
    def __init__(self) -> None:
        self.blocks: list[bytes | Exception] = []
        self.ready = threading.Semaphore(0)
        self.closed = threading.Event()
        self.sizes: list[int] = []
        self.consumed = threading.Event()

    def push(self, item: bytes | Exception) -> None:
        self.blocks.append(item)
        self.ready.release()

    def read(self, size: int) -> bytes:
        self.sizes.append(size)
        # Like pa_simple_read, block until the server has a fragment.
        if not self.ready.acquire(timeout=0.05):
            return b"\x00\x00" * (size // 2)
        item = self.blocks.pop(0)
        self.consumed.set()
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed.set()


class FakeLibrary:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.stream = FakeStream()
        self.rates: list[int] = []

    def open_monitor(self, rate: int) -> FakeStream:
        self.rates.append(rate)
        if self.fail:
            raise _PulseError("Connection refused")
        return self.stream


async def _next_nonsilent(recorder: PulseSystemRecorder) -> bytes:
    async for chunk in recorder.chunks():
        if chunk.strip(b"\x00"):
            return chunk
    raise AssertionError("stream ended")


@run_async
async def test_records_the_default_monitor_at_a_fixed_rate_and_closes_on_stop() -> None:
    library = FakeLibrary()
    recorder = PulseSystemRecorder(library)
    await recorder.start()
    assert library.rates == [SAMPLE_RATE]
    assert recorder.sample_rate == SAMPLE_RATE
    library.stream.push(b"\x01\x00\x02\x00")
    assert await _next_nonsilent(recorder) == b"\x01\x00\x02\x00"
    # Blocks of 50 ms keep the meter and live transcription responsive.
    assert library.stream.sizes[0] == SAMPLE_RATE // 20 * 2
    await recorder.stop()
    assert library.stream.closed.is_set()


@run_async
async def test_paused_audio_is_discarded() -> None:
    library = FakeLibrary()
    recorder = PulseSystemRecorder(library)
    await recorder.start()
    await recorder.pause()
    library.stream.push(b"\x05\x00")
    assert await asyncio.to_thread(library.stream.consumed.wait, 1)
    await recorder.resume()
    library.stream.push(b"\x07\x00")
    assert await _next_nonsilent(recorder) == b"\x07\x00"
    await recorder.stop()


@run_async
async def test_unreachable_sound_server_is_a_system_audio_error() -> None:
    recorder = PulseSystemRecorder(FakeLibrary(fail=True))
    with pytest.raises(SystemAudioError, match="Connection refused"):
        await recorder.start()


@run_async
async def test_read_failure_ends_the_stream_with_an_error_and_closes_it() -> None:
    library = FakeLibrary()
    recorder = PulseSystemRecorder(library)
    await recorder.start()
    library.stream.push(_PulseError("Connection terminated"))
    with pytest.raises(SystemAudioError, match="Connection terminated"):
        async for _chunk in recorder.chunks():
            pass
    await recorder.stop()
    assert library.stream.closed.is_set()
