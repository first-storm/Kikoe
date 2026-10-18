from kikoe.media.devices import AudioDevice, list_devices, list_devices_sync, resolve_device
from tests.helpers import run_async

ENTRIES = [
    {"name": "HDMI", "max_input_channels": 0, "max_output_channels": 8},
    {"name": "Built-in", "max_input_channels": 2, "max_output_channels": 2},
    {"name": "pipewire", "max_input_channels": 64, "max_output_channels": 64},
    {"name": "default", "max_input_channels": 64, "max_output_channels": 64},
]


class FakeBackend:
    def query_devices(self, device: int | None = None, kind: str | None = None) -> object:
        return ENTRIES


def test_devices_are_filtered_by_direction() -> None:
    inputs = list_devices_sync("input", FakeBackend())
    outputs = list_devices_sync("output", FakeBackend())
    assert [d.name for d in inputs] == ["Built-in", "pipewire", "default"]
    assert [d.name for d in outputs] == ["HDMI", "Built-in", "pipewire", "default"]


def test_saved_device_name_resolves_to_its_index() -> None:
    assert resolve_device(list_devices_sync("output", FakeBackend()), "Built-in") == 1


def test_system_default_prefers_the_sound_server_over_the_first_device() -> None:
    assert resolve_device(list_devices_sync("output", FakeBackend()), None) == 2


def test_missing_saved_device_falls_back_to_system_default() -> None:
    assert resolve_device(list_devices_sync("output", FakeBackend()), "Gone") == 2


def test_without_sound_server_alias_portaudio_default_is_used() -> None:
    assert resolve_device([AudioDevice(0, "HDMI")], None) is None


@run_async
async def test_selectable_devices_hide_aliases_covered_by_system_default() -> None:
    names = [d.name for d in await list_devices("output", FakeBackend())]
    assert names == ["HDMI", "Built-in"]
