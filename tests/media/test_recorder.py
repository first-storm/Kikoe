"""The PortAudio boundary is tested with a fake device, never a real microphone."""

from collections.abc import Buffer, Callable
from dataclasses import dataclass

import pytest

from kikoe.media.recorder import RecorderError, SoundDeviceRecorder, _Status
from tests.helpers import run_async


@dataclass(frozen=True, slots=True)
class FakeStatus:
    input_overflow: bool = False


class FakeStream:
    def __init__(self, callback: Callable[[Buffer, int, object, _Status], None]) -> None:
        self.callback = callback
        self.active = False
        self.closes = 0

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.closes += 1

    def feed(self, data: bytes, overflow: bool = False) -> None:
        self.callback(data, len(data) // 2, object(), FakeStatus(overflow))


class FakeBackend:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.stream: FakeStream | None = None
        self.device: int | None = None

    def query_devices(self, device: int | None = None, kind: str | None = None) -> object:
        if self.fail:
            raise RuntimeError("Permission denied")
        if kind is None:
            return [
                {"name": "Built-in", "max_input_channels": 2},
                {"name": "USB Mic", "max_input_channels": 1},
            ]
        assert kind == "input"
        return {"default_samplerate": 48000.0}

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
    ) -> FakeStream:
        assert (samplerate, channels, dtype, blocksize) == (48000, 1, "int16", 2400)
        assert latency == 0.2
        self.device = device
        self.stream = FakeStream(callback)
        return self.stream


@run_async
async def test_capture_uses_native_sample_rate_and_drains_before_stop() -> None:
    backend = FakeBackend()
    recorder = SoundDeviceRecorder(backend=backend)
    await recorder.start()
    assert recorder.sample_rate == 48000
    assert backend.stream is not None
    backend.stream.feed(b"\x01\x00" * 8)
    await recorder.stop()
    assert [chunk async for chunk in recorder.chunks()] == [b"\x01\x00" * 8]
    await recorder.stop()
    assert backend.stream.closes == 1


@run_async
async def test_pause_discards_pause_audio_and_resume_restarts_device() -> None:
    backend = FakeBackend()
    recorder = SoundDeviceRecorder(backend=backend)
    await recorder.start()
    assert backend.stream is not None
    backend.stream.feed(b"\x01\x00")
    await recorder.pause()
    assert not backend.stream.active
    backend.stream.feed(b"\x02\x00")
    await recorder.resume()
    assert backend.stream.active
    backend.stream.feed(b"\x03\x00")
    await recorder.stop()
    assert [chunk async for chunk in recorder.chunks()] == [b"\x01\x00", b"\x03\x00"]


@run_async
async def test_missing_device_has_a_user_presentable_error() -> None:
    recorder = SoundDeviceRecorder(backend=FakeBackend(fail=True))
    with pytest.raises(RecorderError, match="microphone permissions"):
        await recorder.start()
    await recorder.stop()


@run_async
async def test_input_overflow_reports_failure_after_draining_accepted_audio() -> None:
    backend = FakeBackend()
    recorder = SoundDeviceRecorder(backend=backend)
    await recorder.start()
    assert backend.stream is not None
    backend.stream.feed(b"\x01\x00")
    backend.stream.feed(b"\x02\x00", overflow=True)
    received: list[bytes] = []
    with pytest.raises(RecorderError, match="lost audio"):
        async for chunk in recorder.chunks():
            received.append(chunk)
    assert received == [b"\x01\x00"]
    await recorder.stop()


@run_async
async def test_slow_consumer_uses_bounded_memory_and_reports_failure() -> None:
    backend = FakeBackend()
    recorder = SoundDeviceRecorder(backend=backend)
    await recorder.start()
    assert backend.stream is not None
    for _ in range(100):
        backend.stream.feed(b"\x01\x00")
    received: list[bytes] = []
    with pytest.raises(RecorderError, match="fast enough"):
        async for chunk in recorder.chunks():
            received.append(chunk)
    assert len(received) == 64
    await recorder.stop()


@run_async
async def test_disconnected_device_wakes_a_waiting_consumer() -> None:
    backend = FakeBackend()
    recorder = SoundDeviceRecorder(backend=backend)
    await recorder.start()
    assert backend.stream is not None
    backend.stream.active = False
    with pytest.raises(RecorderError, match="disconnected"):
        async for _chunk in recorder.chunks():
            pytest.fail("A disconnected device must not produce fake audio")
    await recorder.stop()


@run_async
async def test_stopping_before_first_audio_ends_the_iterator() -> None:
    recorder = SoundDeviceRecorder(backend=FakeBackend())
    await recorder.start()
    await recorder.stop()
    assert [chunk async for chunk in recorder.chunks()] == []


@run_async
async def test_recorder_opens_the_saved_microphone_by_name() -> None:
    backend = FakeBackend()
    recorder = SoundDeviceRecorder("USB Mic", backend=backend)
    await recorder.start()
    assert backend.device == 1
    await recorder.stop()


@run_async
async def test_recorder_falls_back_to_default_when_saved_microphone_is_missing() -> None:
    backend = FakeBackend()
    recorder = SoundDeviceRecorder("Unplugged", backend=backend)
    await recorder.start()
    assert backend.device is None
    await recorder.stop()
