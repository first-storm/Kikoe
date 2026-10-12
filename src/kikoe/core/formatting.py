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
