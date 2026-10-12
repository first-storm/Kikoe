"""Locale-independent pieces of how times and transcripts are labelled.

Translated wording (“Today”, “2 recordings”) is added by the UI on top of these.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum

from kikoe.core.models import Segment
from kikoe.core.validation import Capability


class RelativeDay(StrEnum):
    """How far back a date lies, which decides how the UI words it."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_YEAR = "this-year"
    OLDER = "older"


def format_duration(duration: timedelta) -> str:
    """A length such as ``0:47``, ``42:10`` or ``1:02:03``, rounded to whole seconds."""
    hours, minutes, seconds = _split(max(0, round(duration.total_seconds())))
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes}:{seconds:02}"


def format_timestamp(position: timedelta) -> str:
    """A segment start such as ``00:09`` or ``1:02:03``.

    Truncated rather than rounded so a line never claims to start after it does.
    """
    hours, minutes, seconds = _split(max(0, int(position.total_seconds())))
    if hours:
        return f"{hours}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


class TimestampParseError(ValueError):
    """A user-entered timestamp could not be understood."""


def format_timestamp_range(start: timedelta | None, end: timedelta | None) -> str:
    """A segment's time span with start and end joined by an en-dash.

    Shows whichever side exists; empty when neither does so the caller can hide
    the label. The separator has no spaces to fit the narrow meta column.
    """
    if start is not None and end is not None:
        return f"{format_timestamp(start)}{_RANGE_SEPARATOR}{format_timestamp(end)}"
    if start is not None:
        return format_timestamp(start)
    if end is not None:
        return format_timestamp(end)
    return ""


def format_timestamp_input(position: timedelta) -> str:
    """Editable text for ``position``, keeping milliseconds when present.

    Display truncates to whole seconds, but the editor must not silently drop
    the sub-second part that subtitle export relies on.
    """
    total_ms = max(0, round(position / timedelta(milliseconds=1)))
    seconds, millis = divmod(total_ms, 1000)
    base = format_timestamp(timedelta(seconds=seconds))
    return f"{base}.{millis:03}" if millis else base


def parse_timestamp(value: str) -> timedelta | None:
    """Parse ``SS``, ``MM:SS`` or ``H:MM:SS`` with an optional fractional part.

    Empty text means the timestamp is cleared and returns None. Raises
    :class:`TimestampParseError` for anything else unparseable.
    """
    text = value.strip().replace(",", ".")
    if not text:
        return None
    if text.startswith("-"):
        raise TimestampParseError(f"Cannot parse timestamp: {value!r}.")
    parts = text.split(":")
    if len(parts) > 3 or any(not part for part in parts):
        raise TimestampParseError(f"Cannot parse timestamp: {value!r}.")
    try:
        *head, last = parts
        seconds = float(last)
        numbers = [int(part) for part in head] + [seconds]
    except ValueError:
        raise TimestampParseError(f"Cannot parse timestamp: {value!r}.") from None
    if any(number < 0 for number in numbers):
        raise TimestampParseError(f"Cannot parse timestamp: {value!r}.")
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
    elif len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]
    else:
        hours, minutes, seconds = 0, 0, numbers[0]
    if minutes >= 60 or seconds >= 60:
        raise TimestampParseError(f"Cannot parse timestamp: {value!r}.")
    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def relative_day(when: datetime, now: datetime) -> RelativeDay:
    """Compare calendar dates in ``now``'s time zone (pass the local time)."""
    day = when.astimezone(now.tzinfo).date()
    today = now.date()
    if day == today:
        return RelativeDay.TODAY
    if day == today - timedelta(days=1):
        return RelativeDay.YESTERDAY
    if day.year == today.year:
        return RelativeDay.THIS_YEAR
    return RelativeDay.OLDER


def transcript_features(segments: Iterable[Segment]) -> tuple[Capability, ...]:
    """What a finished transcript actually contains, in display order."""
    has_times = has_speakers = False
    for segment in segments:
        has_times = has_times or segment.start is not None
        has_speakers = has_speakers or segment.speaker_id is not None
    pairs = ((has_times, Capability.TIMESTAMPS), (has_speakers, Capability.DIARIZATION))
    return tuple(capability for present, capability in pairs if present)


def _split(total_seconds: int) -> tuple[int, int, int]:
    hours, rest = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return hours, minutes, seconds


_RANGE_SEPARATOR = "–"  # noqa: RUF001 -- en-dash shown in the UI; escaped form hurts readability
"""En-dash joining a span's start and end; no spaces to fit the narrow meta column."""


SPEAKER_COLOR_COUNT = 4
"""Distinct speaker colors before they repeat (blue, orange, green, purple)."""


def speaker_order(segments: Iterable[Segment]) -> dict[str, int]:
    """Number speakers 0, 1, … by first appearance; unnamed ones are shown as “Speaker n+1”.

    The color slot is the number modulo :data:`SPEAKER_COLOR_COUNT`, so colors stay stable.
    """
    order: dict[str, int] = {}
    for segment in segments:
        if segment.speaker_id is not None and segment.speaker_id not in order:
            order[segment.speaker_id] = len(order)
    return order
