"""Microphone and system audio combined into one stream, with fake sources."""

import asyncio
import struct
from collections.abc import AsyncIterator

import pytest

from kikoe.media.mixed_recorder import MixedRecorder
from kikoe.media.recorder import RecorderError
from tests.helpers import run_async


def _pcm(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _values(chunk: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(chunk) // 2}h", chunk))


class FakeRecorder:
    def __init__(self, name: str, rate: int, log: list[str], fail_start: bool = False) -> None:
        self.sample_rate = 0
        self._rate = rate
        self._name = name
        self._log = log
        self._fail_start = fail_start
        self._queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()

    async def start(self) -> None:
        self._log.append(f"{self._name} start")
        if self._fail_start:
            raise RecorderError("cannot open")
        self.sample_rate = self._rate

    async def pause(self) -> None:
        self._log.append(f"{self._name} pause")

    async def resume(self) -> None:
        self._log.append(f"{self._name} resume")

    async def stop(self) -> None:
        self._log.append(f"{self._name} stop")
        self._queue.put_nowait(None)

    def feed(self, item: bytes | Exception) -> None:
        self._queue.put_nowait(item)

    async def chunks(self) -> AsyncIterator[bytes]:
        while (item := await self._queue.get()) is not None:
            if isinstance(item, Exception):
                raise item
            yield item


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


@run_async
async def test_system_audio_starts_first_and_output_follows_the_microphone_rate() -> None:
    log: list[str] = []
    microphone, system = FakeRecorder("mic", 16000, log), FakeRecorder("system", 16000, log)
    recorder = MixedRecorder(microphone, system)
    await recorder.start()
    assert log == ["system start", "mic start"]
    assert recorder.sample_rate == 16000


@run_async
async def test_a_failed_microphone_closes_system_audio() -> None:
    log: list[str] = []
    recorder = MixedRecorder(
        FakeRecorder("mic", 16000, log, fail_start=True), FakeRecorder("system", 16000, log)
    )
    with pytest.raises(RecorderError):
        await recorder.start()
    assert log[-1] == "system stop"


@run_async
async def test_mixes_both_sources_and_pads_missing_system_audio_with_silence() -> None:
    log: list[str] = []
    microphone, system = FakeRecorder("mic", 8000, log), FakeRecorder("system", 8000, log)
    recorder = MixedRecorder(microphone, system)
    await recorder.start()
    stream = recorder.chunks()
    microphone.feed(_pcm(0))
    await anext(stream)
    system.feed(_pcm(10, 20))
    await _settle()
    microphone.feed(_pcm(1, 2, 3, 4))
    first = await anext(stream)
    microphone.feed(_pcm(5, 5))
    second = await anext(stream)
    assert _values(first) == [11, 22, 3, 4]
    assert _values(second) == [5, 5]
    await recorder.stop()
    assert [chunk async for chunk in stream] == []


@run_async
async def test_system_audio_is_resampled_to_the_microphone_rate() -> None:
    log: list[str] = []
    microphone, system = FakeRecorder("mic", 8000, log), FakeRecorder("system", 16000, log)
    recorder = MixedRecorder(microphone, system)
    await recorder.start()
    stream = recorder.chunks()
    microphone.feed(_pcm(0, 0))
    await anext(stream)
    system.feed(_pcm(*[100] * 8))
    await _settle()
    microphone.feed(_pcm(0, 0, 0, 0))
    # The resampler trails by one input sample, which starts as silence.
    assert _values(await anext(stream)) == [0, 100, 100, 100]
    await recorder.stop()


@run_async
async def test_system_audio_lag_is_capped_so_drift_cannot_build_up() -> None:
    log: list[str] = []
    microphone, system = FakeRecorder("mic", 10, log), FakeRecorder("system", 10, log)
    recorder = MixedRecorder(microphone, system)
    await recorder.start()
    stream = recorder.chunks()
    microphone.feed(_pcm(0))
    await anext(stream)
    # At 10 Hz the cap keeps five samples; the oldest ones are dropped.
    system.feed(_pcm(*range(1, 11)))
    await _settle()
    microphone.feed(_pcm(0, 0, 0, 0, 0, 0))
    assert _values(await anext(stream)) == [6, 7, 8, 9, 10, 0]
    await recorder.stop()


@run_async
async def test_a_system_audio_error_ends_the_mixed_capture() -> None:
    log: list[str] = []
    microphone, system = FakeRecorder("mic", 8000, log), FakeRecorder("system", 8000, log)
    recorder = MixedRecorder(microphone, system)
    await recorder.start()
    stream = recorder.chunks()
    microphone.feed(_pcm(0))
    await anext(stream)
    system.feed(RecorderError("system audio stopped"))
    await _settle()
    microphone.feed(_pcm(1))
    with pytest.raises(RecorderError, match="system audio stopped"):
        await anext(stream)
    await recorder.stop()


@run_async
async def test_pause_resume_and_stop_reach_both_sources() -> None:
    log: list[str] = []
    recorder = MixedRecorder(FakeRecorder("mic", 8000, log), FakeRecorder("system", 8000, log))
    await recorder.start()
    log.clear()
    await recorder.pause()
    await recorder.resume()
    await recorder.stop()
    assert sorted(log) == sorted(
        ["mic pause", "system pause", "mic resume", "system resume", "mic stop", "system stop"]
    )
