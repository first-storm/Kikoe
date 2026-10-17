import asyncio
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.chunking import TimeSpan
from kikoe.media import ffmpeg, transcode

# Captured from `ffmpeg ... -progress pipe:1` (two reports; the last one is final).
PROGRESS = """\
bitrate=  27.1kbits/s
out_time_us=1500000
out_time=00:00:01.500000
progress=continue
total_size=10332
out_time_us=3000000
out_time_ms=3000000
out_time=00:00:03.000000
speed= 206x
progress=end
"""

# Captured from `ffmpeg -af silencedetect=... -f null -` at log level info.
SILENCE_LOG = """\
Input #0, wav, from 'clip.wav':
  Duration: 00:00:09.00, bitrate: 705 kb/s
[Parsed_silencedetect_0 @ 0x7f16e4002e40] silence_start: -0.0123
[Parsed_silencedetect_0 @ 0x7f16e4002e40] silence_end: 0.8 | silence_duration: 0.81
[Parsed_silencedetect_0 @ 0x7f16e4002e40] silence_start: 1.999955
[Parsed_silencedetect_0 @ 0x7f16e4002e40] silence_end: 3 | silence_duration: 1.000045
[Parsed_silencedetect_0 @ 0x7f16e4002e40] silence_start: 8.5
size=N/A time=00:00:09.00 bitrate=N/A speed=1.2e+03x
"""

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_progress_duration_is_the_last_report() -> None:
    assert transcode.parse_progress_duration(PROGRESS) == timedelta(seconds=3)


def test_progress_without_output_time_has_no_duration() -> None:
    assert transcode.parse_progress_duration("out_time_us=N/A\nprogress=end\n") is None


def test_silences_are_paired_and_clamped() -> None:
    assert transcode.parse_silences(SILENCE_LOG) == (
        TimeSpan(timedelta(0), timedelta(seconds=0.8)),
        TimeSpan(timedelta(seconds=1.999955), timedelta(seconds=3)),
    )


@needs_ffmpeg
@run_async
async def test_converts_to_opus_and_finds_the_pause(tmp_path: Path) -> None:
    clip = tmp_path / "clip.wav"
    await ffmpeg.run(
        *("-f", "lavfi", "-i", "sine=frequency=440:duration=2"),
        *("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono"),
        *("-filter_complex", "[0][1]concat=n=2:v=0:a=1[a0];[a0]atrim=0:3,aresample=16000[a]"),
        *("-map", "[a]", "-y", str(clip)),
    )
    upload = tmp_path / "upload.ogg"

    duration = await transcode.to_upload(clip, upload)
    part = await transcode.to_upload(
        clip, tmp_path / "part.ogg", timedelta(seconds=1), timedelta(seconds=2.5)
    )
    silences = await transcode.find_silences(clip)

    assert upload.read_bytes()[:4] == b"OggS"
    assert abs(duration - timedelta(seconds=3)) < timedelta(seconds=0.1)
    assert abs(part - timedelta(seconds=1.5)) < timedelta(seconds=0.1)
    assert len(silences) == 1
    assert abs(silences[0].start - timedelta(seconds=2)) < timedelta(seconds=0.1)


@needs_ffmpeg
@run_async
async def test_unreadable_input_names_the_file(tmp_path: Path) -> None:
    bogus = tmp_path / "notes.txt"
    bogus.write_text("not audio")

    with pytest.raises(ffmpeg.FFmpegError, match=r"Could not read “notes\.txt”"):
        await transcode.to_upload(bogus, tmp_path / "out.ogg")


@run_async
async def test_missing_ffmpeg_is_not_blamed_on_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("KIKOE_FFMPEG", raising=False)
    monkeypatch.setenv("PATH", "")

    with pytest.raises(ffmpeg.FFmpegNotFoundError, match="not found"):
        await transcode.to_upload(tmp_path / "a.wav", tmp_path / "out.ogg")


@needs_ffmpeg
@run_async
async def test_capture_is_saved_losslessly_before_upload(tmp_path: Path) -> None:
    import asyncio
    import struct

    pcm = struct.pack("<h", 1234) * 48000
    source = tmp_path / "capture.pcm"
    await asyncio.to_thread(source.write_bytes, pcm)
    flac = tmp_path / "capture.flac"
    await transcode.pcm_to_flac(source, flac, 48000)
    decoded = tmp_path / "decoded.pcm"
    await ffmpeg.run("-i", str(flac), "-f", "s16le", "-y", str(decoded))
    assert decoded.read_bytes() == pcm
    assert not flac.with_suffix(".part.flac").exists()
    duration = await transcode.to_upload(flac, tmp_path / "upload.ogg")
    assert abs(duration - timedelta(seconds=1)) < timedelta(milliseconds=100)


@run_async
async def test_failed_flac_conversion_preserves_the_pcm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "capture.pcm"
    source.write_bytes(b"\0\0")
    destination = tmp_path / "capture.flac"

    async def fail(*_args: str, loglevel: str = "error") -> ffmpeg.FFmpegOutput:
        raise ffmpeg.FFmpegError("Conversion interrupted")

    monkeypatch.setattr(ffmpeg, "run", fail)
    with pytest.raises(ffmpeg.FFmpegError):
        await transcode.pcm_to_flac(source, destination, 16000)
    assert source.read_bytes() == b"\0\0"
    assert not destination.exists()


@needs_ffmpeg
@run_async
async def test_upload_formats_use_the_expected_container_and_duration(
    tmp_path: Path,
) -> None:
    from kikoe.core.upload import FLAC, MP3, WAV

    source = tmp_path / "source.wav"
    await ffmpeg.run("-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(source))
    # ffmpeg starts MP3 files with an ID3v2 tag.
    for format, magic in ((FLAC, b"fLaC"), (WAV, b"RIFF"), (MP3, b"ID3")):
        destination = tmp_path / f"upload{format.suffix}"
        duration = await transcode.to_upload(source, destination, format=format)
        content = await asyncio.to_thread(destination.read_bytes)
        assert content.startswith(magic)
        assert abs(duration - timedelta(seconds=1)) < timedelta(milliseconds=100)
