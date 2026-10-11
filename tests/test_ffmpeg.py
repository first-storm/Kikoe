import sys
from pathlib import Path

import pytest

from kikoe.media import ffmpeg


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "ffmpeg"
    fake.touch()
    monkeypatch.setenv("KIKOE_FFMPEG", str(fake))

    assert ffmpeg.find_ffmpeg() == fake


def test_env_override_missing_file_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KIKOE_FFMPEG", str(tmp_path / "nope"))

    with pytest.raises(ffmpeg.FFmpegError, match="missing file"):
        ffmpeg.find_ffmpeg()


def test_bundled_copy_next_to_frozen_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    bundled.touch()
    monkeypatch.delenv("KIKOE_FFMPEG", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "kikoe"))

    assert ffmpeg.find_ffmpeg() == bundled


def test_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIKOE_FFMPEG", raising=False)
    monkeypatch.setenv("PATH", "")

    with pytest.raises(ffmpeg.FFmpegError, match="not found"):
        ffmpeg.find_ffmpeg()
