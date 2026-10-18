"""Playback streams audio without real speakers, large buffers or leaked resources."""

import asyncio
import threading
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path

import pytest

from kikoe.media import player
from kikoe.media.player import PlaybackError, play_audio
from tests.helpers import run_async


class FakeStream:
    def __init__(self) -> None:
        self.data: list[bytes] = []
        self.started = False
        self.stopped = False
        self.aborted = False
        self.closed = False

    def start(self) -> None:
        self.started = True

    def write(self, data: bytes) -> bool:
        assert self.started and not self.closed
        self.data.append(data)
        return False

    def stop(self) -> None:
        self.stopped = True

    def abort(self) -> None:
        self.aborted = True

    def close(self) -> None:
        self.closed = True


class FakeOutput:
    def __init__(self) -> None:
        self.stream = FakeStream()
        self.device: int | None = None

    def query_devices(self, device: int | None = None, kind: str | None = None) -> object:
        if kind is None:
            return [
                {"name": "HDMI", "max_output_channels": 8},
                {"name": "Headset", "max_output_channels": 2},
            ]
        assert kind == "output"
        return {"default_samplerate": 48000}

    def RawOutputStream(  # noqa: N802  # Matches the external API.
        self, *, device: int | None, samplerate: int, channels: int, dtype: str
    ) -> FakeStream:
        self.device = device
        assert (samplerate, channels, dtype) == (48000, 1, "int16")
        return self.stream


@run_async
async def test_playback_streams_requested_sample_and_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = FakeOutput()

    async def decode(
        source: Path, rate: int, start: float, end: float | None
    ) -> AsyncGenerator[bytes]:
        assert (source, rate, start, end) == (tmp_path / "audio.flac", 48000, 2, 5)
        yield b"\x01\0" * 2400
        yield b"\x02\0" * 2400

    monkeypatch.setattr(player, "decode_audio", decode)
    await play_audio(
        tmp_path / "audio.flac",
        start=timedelta(seconds=2),
        end=timedelta(seconds=5),
        backend=device,
    )
    assert len(device.stream.data) == 2
    assert device.stream.stopped and device.stream.closed
    assert not device.stream.aborted


@run_async
async def test_cancelled_playback_closes_decoder_and_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = FakeOutput()
    waiting = asyncio.Event()
    decoder_closed = False

    async def decode(
        source: Path, rate: int, start: float, end: float | None
    ) -> AsyncGenerator[bytes]:
        nonlocal decoder_closed
        try:
            yield bytes(20)
            waiting.set()
            await asyncio.Event().wait()
        finally:
            decoder_closed = True

    monkeypatch.setattr(player, "decode_audio", decode)
    task = asyncio.create_task(play_audio(tmp_path / "audio.flac", backend=device))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert decoder_closed and device.stream.closed and device.stream.aborted


@run_async
async def test_device_open_cancellation_cannot_leak_a_stream(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowOutput(FakeOutput):
        def RawOutputStream(  # noqa: N802  # Matches the external API.
            self, *, device: int | None, samplerate: int, channels: int, dtype: str
        ) -> FakeStream:
            entered.set()
            release.wait(timeout=5)
            return super().RawOutputStream(
                device=device, samplerate=samplerate, channels=channels, dtype=dtype
            )

    device = SlowOutput()
    task = asyncio.create_task(play_audio(tmp_path / "audio.flac", backend=device))
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert device.stream.closed


@run_async
async def test_decoder_failure_closes_stream_and_reports_playback_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = FakeOutput()

    async def decode(
        source: Path, rate: int, start: float, end: float | None
    ) -> AsyncGenerator[bytes]:
        yield bytes(20)
        raise OSError("Missing audio")

    monkeypatch.setattr(player, "decode_audio", decode)
    with pytest.raises(PlaybackError, match="Could not play"):
        await play_audio(tmp_path / "missing.flac", backend=device)
    assert device.stream.closed and device.stream.aborted


@run_async
async def test_playback_uses_the_saved_output_device_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    device = FakeOutput()

    async def decode(
        source: Path, rate: int, start: float, end: float | None
    ) -> AsyncGenerator[bytes]:
        yield b"\x01\0" * 2400

    monkeypatch.setattr(player, "decode_audio", decode)
    await play_audio(tmp_path / "audio.flac", device="Headset", backend=device)
    assert device.device == 1
