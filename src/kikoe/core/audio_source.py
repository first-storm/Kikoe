"""Which sounds a recording captures."""

from enum import StrEnum


class AudioSource(StrEnum):
    """Values double as the `win.audio-source` action state and the settings value."""

    MICROPHONE = "microphone"
    SYSTEM = "system"
    MIXED = "mixed"
