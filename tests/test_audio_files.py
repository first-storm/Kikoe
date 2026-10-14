from pathlib import Path

import pytest

from kikoe.storage.audio_files import AudioFiles
from kikoe.storage.errors import LibraryError


def test_stored_audio_keeps_the_original_extension_in_lower_case(tmp_path: Path) -> None:
    source = tmp_path / "Talk.M4A"
    source.write_bytes(b"audio")
    files = AudioFiles(tmp_path / "library" / "audio")

    stored = files.store(source, "r1")

    assert stored == tmp_path / "library" / "audio" / "r1.m4a"
    assert stored.read_bytes() == b"audio"
    assert source.read_bytes() == b"audio"


def test_a_missing_source_is_reported_to_the_user_and_leaves_no_partial_file(
    tmp_path: Path,
) -> None:
    files = AudioFiles(tmp_path / "audio")

    with pytest.raises(LibraryError, match="Could not copy the audio file"):
        files.store(tmp_path / "missing.wav", "r1")

    assert list((tmp_path / "audio").glob("r1*")) == []


def test_discarding_a_file_that_is_already_gone_is_not_an_error(tmp_path: Path) -> None:
    AudioFiles(tmp_path).discard(tmp_path / "gone.wav")


def test_discarding_removes_the_file(tmp_path: Path) -> None:
    kept = tmp_path / "kept.wav"
    kept.write_bytes(b"x")

    AudioFiles(tmp_path).discard(kept)

    assert not kept.exists()
