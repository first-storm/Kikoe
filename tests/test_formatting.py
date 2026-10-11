from datetime import UTC, datetime, timedelta, timezone

import pytest

from kikoe.core.formatting import (
    RelativeDay,
    format_duration,
    format_timestamp,
    relative_day,
    transcript_features,
)
from kikoe.core.models import Segment
from kikoe.core.validation import Capability


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0:00"),
        (47, "0:47"),
        (47.6, "0:48"),
        (2530, "42:10"),
        (3723, "1:02:03"),
        (-5, "0:00"),
    ],
)
def test_duration_is_clock_style_and_rounded(seconds: float, expected: str) -> None:
    assert format_duration(timedelta(seconds=seconds)) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "00:00"), (9.99, "00:09"), (754, "12:34"), (3723.5, "1:02:03")],
)
def test_timestamp_truncates_and_pads_minutes(seconds: float, expected: str) -> None:
    assert format_timestamp(timedelta(seconds=seconds)) == expected


def test_relative_day_uses_the_local_calendar_date() -> None:
    tokyo = timezone(timedelta(hours=9))
    now = datetime(2026, 10, 12, 8, 0, tzinfo=tokyo)

    # 23:30 UTC on Oct 11 is already Oct 12 in Tokyo.
    assert relative_day(datetime(2026, 10, 11, 23, 30, tzinfo=UTC), now) is RelativeDay.TODAY
    assert relative_day(datetime(2026, 10, 11, 12, 0, tzinfo=UTC), now) is RelativeDay.YESTERDAY
    assert relative_day(datetime(2026, 1, 2, 12, 0, tzinfo=UTC), now) is RelativeDay.THIS_YEAR
    assert relative_day(datetime(2025, 12, 31, 12, 0, tzinfo=UTC), now) is RelativeDay.OLDER


def test_transcript_features_reflect_what_segments_contain() -> None:
    plain = Segment("Hello")
    timed = Segment("Hi", start=timedelta(seconds=1))
    spoken = Segment("Hey", speaker_id="1")

    assert transcript_features([plain]) == ()
    assert transcript_features([plain, timed]) == (Capability.TIMESTAMPS,)
    assert transcript_features([spoken, timed]) == (
        Capability.TIMESTAMPS,
        Capability.DIARIZATION,
    )
