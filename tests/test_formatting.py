from datetime import UTC, datetime, timedelta, timezone

import pytest

from kikoe.core.formatting import (
    RelativeDay,
    TimestampParseError,
    format_duration,
    format_timestamp,
    format_timestamp_input,
    format_timestamp_range,
    parse_timestamp,
    relative_day,
)
from kikoe.core.validation import SegmentEditError, validate_segment_edit


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


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (9, 14, "00:09–00:14"),  # noqa: RUF001 -- asserts the en-dash range separator
        (3723, 3850, "1:02:03–1:04:10"),  # noqa: RUF001 -- asserts the en-dash range separator
        (9, None, "00:09"),
        (None, 14, "00:14"),
        (None, None, ""),
    ],
)
def test_timestamp_range_shows_whichever_side_exists(
    start: float | None, end: float | None, expected: str
) -> None:
    assert (
        format_timestamp_range(
            timedelta(seconds=start) if start is not None else None,
            timedelta(seconds=end) if end is not None else None,
        )
        == expected
    )


def test_timestamp_input_keeps_milliseconds_when_present() -> None:
    assert format_timestamp_input(timedelta(seconds=9, milliseconds=500)) == "00:09.500"
    assert format_timestamp_input(timedelta(seconds=9)) == "00:09"
    assert format_timestamp_input(timedelta(hours=1, minutes=2, seconds=3)) == "1:02:03"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", None),
        ("   ", None),
        ("9", timedelta(seconds=9)),
        ("9.5", timedelta(seconds=9.5)),
        ("00:09", timedelta(seconds=9)),
        ("01:02:03.500", timedelta(hours=1, minutes=2, seconds=3, milliseconds=500)),
        ("1:02:03,500", timedelta(hours=1, minutes=2, seconds=3, milliseconds=500)),
        ("1:02:03", timedelta(hours=1, minutes=2, seconds=3)),
        ("9.", timedelta(seconds=9)),
        (".5", timedelta(seconds=0.5)),
        ("100:00:00", timedelta(hours=100)),
    ],
)
def test_parse_timestamp_accepts_clock_styles(text: str, expected: timedelta | None) -> None:
    assert parse_timestamp(text) == expected


@pytest.mark.parametrize(
    "text",
    ["abc", "12:99", "-1:00", "1:2:3:4", ":", "1::2", "--5", "1:-2", "75", "60:00", "."],
)
def test_parse_timestamp_rejects_garbage(text: str) -> None:
    with pytest.raises(TimestampParseError):
        parse_timestamp(text)


@pytest.mark.parametrize("text", ["nan", "inf", "1e1", "+5", "1_0", "1:nan", "+1:00"])
def test_parse_timestamp_rejects_what_only_float_would_accept(text: str) -> None:
    with pytest.raises(TimestampParseError):
        parse_timestamp(text)


def test_validate_segment_edit_allows_cleared_timestamps() -> None:
    assert validate_segment_edit("Hi", timedelta(seconds=9), None) is None
    assert validate_segment_edit("Hi", None, None) is None
    assert validate_segment_edit("Hi", timedelta(seconds=9), timedelta(seconds=14)) is None


def test_validate_segment_edit_rejects_empty_text_and_inverted_range() -> None:
    assert validate_segment_edit("  ", timedelta(seconds=1), None) is SegmentEditError.EMPTY_TEXT
    assert (
        validate_segment_edit("Hi", timedelta(seconds=14), timedelta(seconds=9))
        is SegmentEditError.END_BEFORE_START
    )
    assert (
        validate_segment_edit("Hi", timedelta(seconds=9), timedelta(seconds=9))
        is SegmentEditError.END_BEFORE_START
    )
