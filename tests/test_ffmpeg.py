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


def test_frozen_binary_uses_internal_bundle_before_host_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    internal = tmp_path / "_internal"
    internal.mkdir()
    bundled = internal / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    bundled.touch()
    monkeypatch.delenv("KIKOE_FFMPEG", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "kikoe"))
    monkeypatch.setenv("PATH", "")

    assert ffmpeg.find_ffmpeg() == bundled


@pytest.mark.parametrize("output_rate", [16000, 24000])
def test_streaming_resampler_drains_all_audio(output_rate: int) -> None:
    import asyncio
    import struct

    async def run() -> None:
        stream = await ffmpeg.PCMStream.start(48000, output_rate)

        async def feed() -> None:
            for _ in range(4):
                await stream.feed(struct.pack("<h", 1000) * 12000)
            await stream.finish()

        writer = asyncio.create_task(feed())
        try:
            audio = b"".join([chunk async for chunk in stream.chunks()])
            await writer
            assert abs(len(audio) - output_rate * 2) <= 2
            assert len(audio) % 2 == 0
        finally:
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)
            await stream.close()

    try:
        ffmpeg.find_ffmpeg()
    except ffmpeg.FFmpegNotFoundError:
        pytest.skip("ffmpeg not installed")
    asyncio.run(run())


def test_playback_decoder_seeks_and_bounds_pcm(tmp_path: Path) -> None:
    import asyncio
    import wave
    from contextlib import aclosing

    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(bytes(16000 * 2 * 2))

    async def run() -> None:
        async with aclosing(ffmpeg.decode_audio(source, 24000, 0.5, 1.0)) as chunks:
            data = b"".join([chunk async for chunk in chunks])
        assert len(data) == 24000

    try:
        ffmpeg.find_ffmpeg()
    except ffmpeg.FFmpegNotFoundError:
        pytest.skip("ffmpeg not installed")
    asyncio.run(run())
