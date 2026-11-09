"""Mono little-endian int16 PCM helpers for combining capture streams without NumPy."""

import sys
from array import array
from dataclasses import dataclass

_MIN = -32768
_MAX = 32767


def _samples(chunk: bytes) -> array[int]:
    samples = array("h", chunk)
    if sys.byteorder != "little":
        samples.byteswap()
    return samples


def _bytes(samples: array[int]) -> bytes:
    if sys.byteorder != "little":
        samples.byteswap()
    return samples.tobytes()


def mix(first: bytes, second: bytes) -> bytes:
    """Sum two equally long chunks, clipping instead of wrapping on loud overlaps."""
    if len(first) != len(second):
        raise ValueError("Mixed chunks must have the same length")
    a, b = _samples(first), _samples(second)
    return _bytes(array("h", (min(_MAX, max(_MIN, x + y)) for x, y in zip(a, b, strict=True))))


@dataclass(slots=True)
class Resampler:
    """Linear resampling that carries its position across chunks so joins do not click.

    Positions are counted exactly, in steps of 1/``to_rate`` input samples, so hours of
    small chunks produce the same output as one long buffer.
    """

    from_rate: int
    to_rate: int
    _position: int = 0
    """Where the next output sample falls, measured from the previous chunk's last sample."""
    _previous: int = 0

    def __post_init__(self) -> None:
        if self.from_rate <= 0 or self.to_rate <= 0:
            raise ValueError("Sample rates must be positive")

    def process(self, chunk: bytes) -> bytes:
        if self.from_rate == self.to_rate:
            return chunk
        source = _samples(chunk)
        if not source:
            return b""
        output = array("h")
        position, scale, end = self._position, self.to_rate, len(source) * self.to_rate
        # Position p interpolates between input samples p - 1 and p; sample -1 is the
        # previous chunk's last, so the output trails the input by one sample.
        while position < end:
            base, remainder = divmod(position, scale)
            left = self._previous if base == 0 else source[base - 1]
            right = source[base]
            output.append(left + round((right - left) * remainder / scale))
            position += self.from_rate
        self._position = position - end
        self._previous = source[-1]
        return _bytes(output)
