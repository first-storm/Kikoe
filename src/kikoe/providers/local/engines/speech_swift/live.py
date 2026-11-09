"""Live audio for a model that only transcribes whole pieces: where to cut, how to join."""

import io
import math
import sys
import wave
from array import array

SAMPLE_RATE = 16000

# A piece is sent once it is long enough and ends in a pause, or at the cap.
FRAME = 480
"""30 ms of samples, the unit of loudness measurement."""
_MIN_PIECE = 3 * SAMPLE_RATE
_MAX_PIECE = 25 * SAMPLE_RATE
"""Below every model's window (Parakeet's is 30 s)."""
_PAUSE_FRAMES = 20
"""600 ms of quiet ends a piece."""
_QUIET_FLOOR = 200.0
"""RMS of 16-bit samples that always counts as quiet."""
_QUIET_RATIO = 0.1
"""Quieter than this share of the loudest frame counts as quiet."""


def pause_cut(pcm: bytes) -> int | None:
    """Where to end a live piece of 16-bit PCM, in bytes; None to keep listening."""
    samples = array("h")
    samples.frombytes(pcm[: len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if len(samples) < _MIN_PIECE:
        return None
    levels = [_rms(samples, i) for i in range(0, len(samples) - FRAME + 1, FRAME)]
    quiet = max(_QUIET_FLOOR, _QUIET_RATIO * max(levels))
    if all(level < quiet for level in levels[-_PAUSE_FRAMES:]):
        return len(samples) * 2
    if len(samples) < _MAX_PIECE:
        return None
    first = _MIN_PIECE // FRAME
    quietest = min(range(first, len(levels)), key=levels.__getitem__)
    return (quietest * FRAME + FRAME // 2) * 2


def _rms(samples: array[int], start: int) -> float:
    frame = samples[start : start + FRAME]
    return math.sqrt(math.sumprod(frame, frame) / len(frame))


def join_text(left: str, right: str) -> str:
    """Join two pieces of a turn; scripts written without spaces stay without them."""
    left, right = left.strip(), right.strip()
    if not left or not right:
        return left or right
    if _unspaced(left[-1]) or _unspaced(right[0]):
        return left + right
    return f"{left} {right}"


def _unspaced(char: str) -> bool:
    code = ord(char)
    return (
        0x3000 <= code <= 0x9FFF  # CJK punctuation, kana, ideographs
        or 0xAC00 <= code <= 0xD7AF  # Hangul
        or 0xF900 <= code <= 0xFAFF
        or 0xFF00 <= code <= 0xFFEF  # full-width forms
        or 0x0E00 <= code <= 0x0E7F  # Thai
    )


def wav_bytes(pcm: bytes) -> bytes:
    """16 kHz mono 16-bit PCM as a WAV file in memory."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm)
    return buffer.getvalue()
