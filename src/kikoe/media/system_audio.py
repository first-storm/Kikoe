"""Pick the capture backend for an audio source; the only place that checks the platform."""

import logging
import sys

from kikoe.core.audio_source import AudioSource
from kikoe.media.mixed_recorder import MixedRecorder
from kikoe.media.recorder import Recorder, SoundDeviceRecorder
from kikoe.media.system_audio_macos import TapSystemRecorder
from kikoe.media.system_audio_pulse import PulseSystemRecorder, load_library

log = logging.getLogger(__name__)


def system_audio_supported() -> bool:
    """Whether this computer can record what it plays; Windows support is not built yet."""
    if sys.platform == "darwin":
        return True
    if sys.platform.startswith("linux"):
        return load_library() is not None
    return False


def _system_audio_recorder() -> Recorder:
    return TapSystemRecorder() if sys.platform == "darwin" else PulseSystemRecorder()


def create_recorder(source: AudioSource, input_device: str | None) -> Recorder:
    """A recorder for ``source``; the input device applies wherever a microphone is used.

    Without system audio the Record button offers no choice, so a saved system source
    falls back to the microphone it shows.
    """
    if source is not AudioSource.MICROPHONE and not system_audio_supported():
        log.info("System audio is unavailable; recording the microphone")
        source = AudioSource.MICROPHONE
    match source:
        case AudioSource.MICROPHONE:
            return SoundDeviceRecorder(input_device)
        case AudioSource.SYSTEM:
            return _system_audio_recorder()
        case AudioSource.MIXED:
            return MixedRecorder(SoundDeviceRecorder(input_device), _system_audio_recorder())
