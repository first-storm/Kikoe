"""PCM levels used by recording progress."""

import struct

import pytest

from kikoe.core.recording import pcm_level


def test_pcm_level_handles_silence_and_full_scale() -> None:
    assert pcm_level(b"") == 0
    assert pcm_level(b"\0" * 100) == 0
    assert pcm_level(struct.pack("<h", -32768) * 100) == 1
    assert 0 < pcm_level(struct.pack("<h", 1000) * 100) < 1
    with pytest.raises(ValueError, match="complete"):
        pcm_level(b"x")
