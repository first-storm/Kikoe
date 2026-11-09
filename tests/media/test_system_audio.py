"""Choosing a capture backend for each audio source."""

import pytest

from kikoe.core.audio_source import AudioSource
from kikoe.media import system_audio
from kikoe.media.mixed_recorder import MixedRecorder
from kikoe.media.recorder import SoundDeviceRecorder
from kikoe.media.system_audio_macos import TapSystemRecorder
from kikoe.media.system_audio_pulse import PulseSystemRecorder


def test_microphone_source_records_the_chosen_input() -> None:
    assert isinstance(
        system_audio.create_recorder(AudioSource.MICROPHONE, "USB Mic"), SoundDeviceRecorder
    )


@pytest.mark.parametrize(
    ("platform", "expected"), [("darwin", TapSystemRecorder), ("linux", PulseSystemRecorder)]
)
def test_system_source_uses_the_platform_backend(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: type
) -> None:
    monkeypatch.setattr(system_audio.sys, "platform", platform)
    monkeypatch.setattr(system_audio, "system_audio_supported", lambda: True)
    assert isinstance(system_audio.create_recorder(AudioSource.SYSTEM, None), expected)
    assert isinstance(system_audio.create_recorder(AudioSource.MIXED, None), MixedRecorder)


@pytest.mark.parametrize("source", [AudioSource.SYSTEM, AudioSource.MIXED])
def test_saved_system_source_falls_back_to_the_microphone_where_unsupported(
    monkeypatch: pytest.MonkeyPatch, source: AudioSource
) -> None:
    monkeypatch.setattr(system_audio, "system_audio_supported", lambda: False)
    assert isinstance(system_audio.create_recorder(source, None), SoundDeviceRecorder)


def test_windows_has_no_system_audio_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_audio.sys, "platform", "win32")
    assert not system_audio.system_audio_supported()


def test_linux_without_pulseaudio_client_has_no_system_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_audio.sys, "platform", "linux")
    monkeypatch.setattr(system_audio, "load_library", lambda: None)
    assert not system_audio.system_audio_supported()
