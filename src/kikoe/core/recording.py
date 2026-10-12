"""Capture status and PCM measurements independent of microphone and UI libraries."""

import math
import struct
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum


class CaptureState(StrEnum):
    """The controls available during a microphone session."""

    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class CaptureProgress:
    """Only accepted audio frames contribute to the elapsed time."""

    state: CaptureState
    elapsed: timedelta
    level: float


def pcm_level(chunk: bytes) -> float:
    """Return a speech-friendly, normalized RMS level for little-endian int16 PCM."""
    if not chunk:
        return 0.0
    if len(chunk) % 2:
        raise ValueError("PCM must contain complete 16-bit samples")
    samples = struct.iter_unpack("<h", chunk)
    mean = sum(sample * sample for (sample,) in samples) / (len(chunk) // 2)
    if not mean:
        return 0.0
    decibels = 20 * math.log10(math.sqrt(mean) / 32768)
    return max(0.0, min(1.0, (decibels + 60) / 60))
