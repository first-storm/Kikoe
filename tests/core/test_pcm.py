"""Combining capture streams in pure Python."""

import struct

import pytest

from kikoe.core.pcm import Resampler, mix


def _pcm(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def _values(chunk: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(chunk) // 2}h", chunk))


def test_mix_sums_samples_and_clips_instead_of_wrapping() -> None:
    mixed = mix(_pcm(100, 30000, -30000, 0), _pcm(-50, 10000, -10000, 7))
    assert _values(mixed) == [50, 32767, -32768, 7]


def test_mix_rejects_chunks_of_different_length() -> None:
    with pytest.raises(ValueError, match="same length"):
        mix(_pcm(1, 2), _pcm(1))


def test_resampler_passes_audio_through_at_equal_rates() -> None:
    chunk = _pcm(1, 2, 3)
    assert Resampler(48000, 48000).process(chunk) == chunk


def test_resampler_output_length_follows_the_rate_ratio_across_small_chunks() -> None:
    resampler = Resampler(48000, 16000)
    total = sum(len(resampler.process(_pcm(*range(7)))) // 2 for _ in range(300))
    assert abs(total - 7 * 300 / 3) <= 1


def test_resampling_in_chunks_matches_resampling_at_once() -> None:
    samples = [round(1000 * ((i % 37) - 18)) for i in range(441)]
    whole = Resampler(44100, 48000).process(_pcm(*samples))
    pieces = Resampler(44100, 48000)
    joined = b"".join(pieces.process(_pcm(*samples[i : i + 50])) for i in range(0, 441, 50))
    assert joined == whole


def test_upsampling_interpolates_between_neighbors() -> None:
    # The output trails by one input sample, starting from silence.
    assert _values(Resampler(1, 2).process(_pcm(100, 200))) == [0, 50, 100, 150]


def test_resampler_rejects_non_positive_rates() -> None:
    with pytest.raises(ValueError, match="positive"):
        Resampler(0, 48000)
