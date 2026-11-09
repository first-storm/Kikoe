"""Record what macOS plays through a Core Audio process tap (macOS 14.2 or later).

A private aggregate device wraps the tap, so PortAudio can open it like a microphone
and capture reuses `SoundDeviceRecorder`. Kikoe's own process is excluded so playing a
recording or speaker sample during capture is not recorded again. macOS asks for the
System Audio Recording permission on first use; when it is denied the tap delivers
silence rather than an error.
"""

import asyncio
import importlib
import logging
import os
import struct
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol, cast

from kikoe.i18n import _
from kikoe.media.devices import PRIVATE_DEVICE_PREFIX, list_devices_sync
from kikoe.media.recorder import (
    Recorder,
    RecorderError,
    SoundDeviceRecorder,
    SystemAudioError,
)

log = logging.getLogger(__name__)

_DEVICE_WAIT_SECONDS = 1.0


class _TapError(Exception):
    pass


class TapApi(Protocol):
    """The Core Audio calls behind one tap; blocking, so they run off the main thread."""

    def create_tap(self) -> tuple[int, str]: ...
    def create_aggregate(self, name: str, tap_uid: str) -> int: ...
    def destroy_aggregate(self, device: int) -> None: ...
    def destroy_tap(self, tap: int) -> None: ...


class _CoreAudioTaps:
    """PyObjC's CoreAudio bindings are untyped, so they stay behind `TapApi`."""

    def __init__(self) -> None:
        self._audio: Any = importlib.import_module("CoreAudio")  # Untyped PyObjC module.

    def _key(self, name: str) -> str:
        # Dictionary keys are C string macros, which PyObjC exposes as bytes.
        value = cast(bytes | str, getattr(self._audio, name))
        return value.decode() if isinstance(value, bytes) else value

    def _own_process(self) -> list[int]:
        audio = self._audio
        address = audio.AudioObjectPropertyAddress(
            audio.kAudioHardwarePropertyTranslatePIDToProcessObject,
            audio.kAudioObjectPropertyScopeGlobal,
            audio.kAudioObjectPropertyElementMain,
        )
        status, _size, data = audio.AudioObjectGetPropertyData(
            audio.kAudioObjectSystemObject, address, 4, struct.pack("=i", os.getpid()), 4, None
        )
        if status != 0 or data is None:
            log.warning("Could not exclude Kikoe from system audio (status %s)", status)
            return []
        (process,) = struct.unpack("=I", bytes(data))
        return [process] if process else []

    def create_tap(self) -> tuple[int, str]:
        audio = self._audio
        description = audio.CATapDescription.alloc().initMonoGlobalTapButExcludeProcesses_(
            self._own_process()
        )
        description.setName_("Kikoe")
        description.setPrivate_(True)
        status, tap = audio.AudioHardwareCreateProcessTap(description, None)
        if status != 0:
            raise _TapError(f"AudioHardwareCreateProcessTap failed with status {status}")
        return int(tap), str(description.UUID().UUIDString())

    def create_aggregate(self, name: str, tap_uid: str) -> int:
        audio = self._audio
        tap: dict[str, object] = {
            self._key("kAudioSubTapUIDKey"): tap_uid,
            self._key("kAudioSubTapDriftCompensationKey"): True,
        }
        status, device = audio.AudioHardwareCreateAggregateDevice(
            {
                self._key("kAudioAggregateDeviceNameKey"): name,
                self._key("kAudioAggregateDeviceUIDKey"): f"io.github.first_storm.Kikoe.{name}",
                self._key("kAudioAggregateDeviceIsPrivateKey"): True,
                self._key("kAudioAggregateDeviceTapAutoStartKey"): True,
                self._key("kAudioAggregateDeviceTapListKey"): [tap],
            },
            None,
        )
        if status != 0:
            raise _TapError(f"AudioHardwareCreateAggregateDevice failed with status {status}")
        return int(device)

    def destroy_aggregate(self, device: int) -> None:
        self._audio.AudioHardwareDestroyAggregateDevice(device)

    def destroy_tap(self, tap: int) -> None:
        self._audio.AudioHardwareDestroyProcessTap(tap)


def _reinitialize_portaudio() -> None:
    """PortAudio lists devices once, so it must restart to see the new aggregate device.

    Recording stops playback first, and the microphone opens afterwards, so no stream is
    open while this runs.
    """
    sounddevice: Any = importlib.import_module("sounddevice")  # Untyped CFFI module.
    sounddevice._terminate()  # No public way to rescan devices.
    sounddevice._initialize()


class TapSystemRecorder:
    """Owns a tap and its aggregate device for one capture, removing both on stop."""

    def __init__(
        self,
        taps: TapApi | None = None,
        reinitialize: Callable[[], None] = _reinitialize_portaudio,
        device_names: Callable[[], list[str]] | None = None,
        make_recorder: Callable[[str], Recorder] = SoundDeviceRecorder,
    ) -> None:
        self.sample_rate = 0
        self._taps = taps
        self._reinitialize = reinitialize
        self._device_names = device_names or (
            lambda: [device.name for device in list_devices_sync("input")]
        )
        self._make_recorder = make_recorder
        self._tap: int | None = None
        self._aggregate: int | None = None
        self._inner: Recorder | None = None

    async def start(self) -> None:
        try:
            name = await asyncio.to_thread(self._create)
            inner = self._make_recorder(name)
            await inner.start()
        except BaseException as error:
            await asyncio.to_thread(self._destroy)
            if isinstance(error, (_TapError, RecorderError, OSError, ImportError)):
                raise SystemAudioError(
                    _(
                        "Could not record system audio. Allow Kikoe under System Audio "
                        "Recording in Privacy & Security settings. ({detail})"
                    ).format(detail=error)
                ) from error
            raise
        self._inner = inner
        self.sample_rate = inner.sample_rate

    def _create(self) -> str:
        taps = self._taps = self._taps or _CoreAudioTaps()
        self._tap, tap_uid = taps.create_tap()
        name = f"{PRIVATE_DEVICE_PREFIX}{uuid.uuid4().hex[:8]}"
        self._aggregate = taps.create_aggregate(name, tap_uid)
        # The device appears asynchronously; opening by an unknown name would silently
        # fall back to the default microphone.
        deadline = time.monotonic() + _DEVICE_WAIT_SECONDS
        while True:
            self._reinitialize()
            if name in self._device_names():
                return name
            if time.monotonic() >= deadline:
                raise SystemAudioError(_("The system audio device did not appear."))
            time.sleep(0.1)

    def _destroy(self) -> None:
        taps = self._taps
        aggregate, self._aggregate = self._aggregate, None
        tap, self._tap = self._tap, None
        if taps is None:
            return
        try:
            if aggregate is not None:
                taps.destroy_aggregate(aggregate)
        finally:
            if tap is not None:
                taps.destroy_tap(tap)

    def chunks(self) -> AsyncIterator[bytes]:
        if self._inner is None:
            raise SystemAudioError(_("System audio recording has not started."))
        return self._inner.chunks()

    async def pause(self) -> None:
        if self._inner is not None:
            await self._inner.pause()

    async def resume(self) -> None:
        if self._inner is not None:
            await self._inner.resume()

    async def stop(self) -> None:
        inner, self._inner = self._inner, None
        try:
            if inner is not None:
                await inner.stop()
        finally:
            await asyncio.to_thread(self._destroy)
