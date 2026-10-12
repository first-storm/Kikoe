"""List audio devices and map a saved device name to a PortAudio device index."""

import asyncio
import importlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, cast

log = logging.getLogger(__name__)

type DeviceKind = Literal["input", "output"]

# PortAudio's ALSA default is the first hardware device, not the desktop's choice. The
# sound server's ALSA plugins follow the desktop setting, so "System Default" prefers them.
_SYSTEM_DEFAULT_NAMES = ("pipewire", "pulse", "default")


@dataclass(frozen=True, slots=True)
class AudioDevice:
    """Names are stable across sessions; PortAudio indices are not."""

    index: int
    name: str


class DeviceBackend(Protocol):
    """Typed boundary around sounddevice's untyped device list."""

    def query_devices(self, device: int | None = None, kind: str | None = None) -> object: ...


def _backend() -> DeviceBackend:
    return cast(DeviceBackend, importlib.import_module("sounddevice"))


def list_devices_sync(kind: DeviceKind, backend: DeviceBackend | None = None) -> list[AudioDevice]:
    """Every device that can capture or play; blocking, so call it off the main thread."""
    entries = cast(Sequence[Mapping[str, object]], (backend or _backend()).query_devices())
    channels_key = f"max_{kind}_channels"
    devices: list[AudioDevice] = []
    for index, entry in enumerate(entries):
        name = entry.get("name")
        channels = entry.get(channels_key)
        if isinstance(name, str) and isinstance(channels, int) and channels > 0:
            devices.append(AudioDevice(index, name))
    return devices


async def list_devices(kind: DeviceKind, backend: DeviceBackend | None = None) -> list[AudioDevice]:
    """Devices a user can choose, without the aliases that "System Default" already covers."""
    devices = await asyncio.to_thread(list_devices_sync, kind, backend)
    seen: set[str] = set()
    selectable: list[AudioDevice] = []
    for device in devices:
        if device.name in _SYSTEM_DEFAULT_NAMES or device.name in seen:
            continue
        seen.add(device.name)
        selectable.append(device)
    return selectable


def resolve_device(devices: Sequence[AudioDevice], name: str | None) -> int | None:
    """Return a PortAudio index, or None to let PortAudio choose its own default."""
    if name is not None:
        for device in devices:
            if device.name == name:
                return device.index
        log.warning("Audio device %r is unavailable; using the system default", name)
    for preferred in _SYSTEM_DEFAULT_NAMES:
        for device in devices:
            if device.name == preferred:
                return device.index
    return None


def find_device(
    kind: DeviceKind, name: str | None, backend: DeviceBackend | None = None
) -> int | None:
    """Blocking lookup used when a stream is opened."""
    return resolve_device(list_devices_sync(kind, backend), name)
