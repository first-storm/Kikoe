"""macOS tap lifecycle with a fake Core Audio, so it runs on every platform."""

from collections.abc import AsyncIterator

import pytest

from kikoe.media.devices import PRIVATE_DEVICE_PREFIX
from kikoe.media.recorder import RecorderError, SystemAudioError
from kikoe.media.system_audio_macos import TapSystemRecorder, _TapError
from tests.helpers import run_async


class FakeTaps:
    def __init__(self, log: list[str], fail_aggregate: bool = False) -> None:
        self.log = log
        self.fail_aggregate = fail_aggregate
        self.names: list[str] = []

    def create_tap(self) -> tuple[int, str]:
        self.log.append("create tap")
        return 7, "tap-uid"

    def create_aggregate(self, name: str, tap_uid: str) -> int:
        self.log.append(f"create aggregate {tap_uid}")
        if self.fail_aggregate:
            raise _TapError("status 560947818")
        self.names.append(name)
        return 9

    def destroy_aggregate(self, device: int) -> None:
        self.log.append(f"destroy aggregate {device}")

    def destroy_tap(self, tap: int) -> None:
        self.log.append(f"destroy tap {tap}")


class FakeInner:
    def __init__(self, name: str, log: list[str], fail: bool = False) -> None:
        self.name = name
        self.log = log
        self.fail = fail
        self.sample_rate = 0

    async def start(self) -> None:
        self.log.append(f"open {self.name.startswith(PRIVATE_DEVICE_PREFIX)}")
        if self.fail:
            raise RecorderError("Could not open the microphone.")
        self.sample_rate = 48000

    async def pause(self) -> None: ...
    async def resume(self) -> None: ...

    async def stop(self) -> None:
        self.log.append("close")

    async def chunks(self) -> AsyncIterator[bytes]:
        yield b"\x00\x00"


def _recorder(
    log: list[str], taps: FakeTaps, *, visible: bool = True, inner_fails: bool = False
) -> TapSystemRecorder:
    return TapSystemRecorder(
        taps=taps,
        reinitialize=lambda: log.append("rescan"),
        device_names=lambda: ["MacBook Microphone", *(taps.names if visible else [])],
        make_recorder=lambda name: FakeInner(name, log, inner_fails),
    )


@run_async
async def test_opens_the_tap_device_after_rescanning_and_removes_it_on_stop() -> None:
    log: list[str] = []
    recorder = _recorder(log, FakeTaps(log))
    await recorder.start()
    assert recorder.sample_rate == 48000
    await recorder.stop()
    assert log == [
        "create tap",
        "create aggregate tap-uid",
        "rescan",
        "open True",
        "close",
        "destroy aggregate 9",
        "destroy tap 7",
    ]


@run_async
async def test_a_device_that_never_appears_is_not_replaced_by_the_microphone() -> None:
    log: list[str] = []
    recorder = _recorder(log, FakeTaps(log), visible=False)
    with pytest.raises(SystemAudioError, match="did not appear"):
        await recorder.start()
    assert not any(entry.startswith("open") for entry in log)
    assert log[-2:] == ["destroy aggregate 9", "destroy tap 7"]


@run_async
async def test_failed_aggregate_still_destroys_the_tap() -> None:
    log: list[str] = []
    recorder = _recorder(log, FakeTaps(log, fail_aggregate=True))
    with pytest.raises(SystemAudioError, match="System Audio Recording"):
        await recorder.start()
    assert log[-1] == "destroy tap 7"


@run_async
async def test_open_failure_is_reported_as_system_audio_and_cleans_up() -> None:
    log: list[str] = []
    recorder = _recorder(log, FakeTaps(log), inner_fails=True)
    with pytest.raises(SystemAudioError, match="system audio"):
        await recorder.start()
    assert log[-2:] == ["destroy aggregate 9", "destroy tap 7"]
