"""Prepare audio for upload: compress to Opus and find silences to split long files at.

Opus at 32 kbit/s mono keeps about 1¾ hours under a 25 MB upload limit while staying
clear enough for speech recognition; OpenAI, Groq and most compatible APIs accept Ogg.
Providers that need another encoding name it through ``UploadLimits.format``.
"""

import asyncio
import re
from datetime import timedelta
from pathlib import Path

from kikoe.core.chunking import TimeSpan
from kikoe.core.upload import OGG_OPUS, UploadFormat
from kikoe.i18n import _
from kikoe.media import ffmpeg

_SILENCE_FILTER = "silencedetect=noise=-35dB:d=0.4"
"""Quieter than -35 dB for 0.4 s counts as a pause between words or sentences."""

_OUT_TIME = re.compile(r"^out_time_us=(\d+)$", re.MULTILINE)


async def pcm_to_flac(source: Path, destination: Path, sample_rate: int) -> None:
    """Keep captured speech lossless; a partial output never replaces a finished file."""
    temporary = destination.with_suffix(".part.flac")
    try:
        await ffmpeg.run(
            "-f",
            "s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-i",
            str(source),
            "-c:a",
            "flac",
            "-y",
            str(temporary),
        )
        await asyncio.to_thread(temporary.replace, destination)
    finally:
        await asyncio.to_thread(temporary.unlink, missing_ok=True)


_SILENCE_START = re.compile(r"silence_start: (-?[\d.]+)")
_SILENCE_END = re.compile(r"silence_end: ([\d.]+)")


async def to_upload(
    source: Path,
    destination: Path,
    start: timedelta | None = None,
    end: timedelta | None = None,
    format: UploadFormat = OGG_OPUS,
) -> timedelta:
    """Convert ``source``, or only ``start`` to ``end`` of it, and return the output length."""
    trim: list[str] = []
    if start is not None:
        trim += ["-ss", _seconds(start)]
    if end is not None:
        trim += ["-t", _seconds(end - (start or timedelta()))]
    output = await _run(
        source,
        *trim,
        "-i",
        str(source),
        *format.args,
        "-progress",
        "pipe:1",
        "-nostats",
        "-y",
        str(destination),
    )
    duration = parse_progress_duration(output.stdout)
    if duration is None:
        raise ffmpeg.FFmpegError(_("“{name}” contains no audio.").format(name=source.name))
    return duration


async def find_silences(source: Path) -> tuple[TimeSpan, ...]:
    """Pauses in ``source``, used as cut points when it must be split."""
    output = await _run(
        source, "-i", str(source), "-vn", "-af", _SILENCE_FILTER, "-f", "null", "-", loglevel="info"
    )
    return parse_silences(output.stderr)


async def _run(source: Path, *args: str, loglevel: str = "error") -> ffmpeg.FFmpegOutput:
    """Run ffmpeg on ``source``; a missing ffmpeg is reported as is, other failures per file."""
    try:
        return await ffmpeg.run(*args, loglevel=loglevel)
    except ffmpeg.FFmpegNotFoundError:
        raise
    except ffmpeg.FFmpegError as error:
        raise _conversion_error(source, error) from error


def parse_progress_duration(progress: str) -> timedelta | None:
    """The final ``out_time_us`` of a ``-progress`` report, i.e. the output's length."""
    values = _OUT_TIME.findall(progress)
    if not values:
        return None
    return timedelta(microseconds=int(values[-1]))


def parse_silences(log_text: str) -> tuple[TimeSpan, ...]:
    """Pair ``silence_start``/``silence_end`` lines; a trailing unmatched start is dropped."""
    silences: list[TimeSpan] = []
    start: float | None = None
    for line in log_text.splitlines():
        if match := _SILENCE_START.search(line):
            start = max(0.0, float(match[1]))
        elif (match := _SILENCE_END.search(line)) and start is not None:
            silences.append(TimeSpan(timedelta(seconds=start), timedelta(seconds=float(match[1]))))
            start = None
    return tuple(silences)


def _seconds(value: timedelta) -> str:
    return f"{value.total_seconds():.3f}"


def _conversion_error(source: Path, error: ffmpeg.FFmpegError) -> ffmpeg.FFmpegError:
    return ffmpeg.FFmpegError(
        _(
            "Could not read “{name}”. It may not be an audio file, or it is damaged. ({detail})"
        ).format(name=source.name, detail=error)
    )
