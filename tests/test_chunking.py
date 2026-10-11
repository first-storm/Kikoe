from datetime import timedelta

import pytest

from kikoe.core.chunking import TimeSpan, offset_segments, plan_chunks
from kikoe.core.models import Segment


def s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


def span(start: float, end: float) -> TimeSpan:
    return TimeSpan(s(start), s(end))


def test_short_audio_is_one_chunk() -> None:
    assert plan_chunks(s(50), [span(10, 12)], s(60)) == (span(0, 50),)


def test_without_silences_cuts_hard_at_the_limit() -> None:
    assert plan_chunks(s(130), [], s(60)) == (span(0, 60), span(60, 120), span(120, 130))


def test_cuts_in_the_middle_of_the_last_silence_that_fits() -> None:
    silences = [span(20, 22), span(50, 54), span(70, 72), span(100, 104)]

    assert plan_chunks(s(130), silences, s(60)) == (span(0, 52), span(52, 102), span(102, 130))


def test_silence_whose_middle_is_exactly_at_the_limit_is_used() -> None:
    assert plan_chunks(s(100), [span(58, 62)], s(60)) == (span(0, 60), span(60, 100))


def test_unsorted_silences_are_handled() -> None:
    silences = [span(50, 54), span(20, 22)]

    assert plan_chunks(s(100), silences, s(60))[0] == span(0, 52)


def test_non_positive_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        plan_chunks(s(10), [], timedelta())


def test_offset_shifts_times_and_keeps_missing_ones() -> None:
    segments = [Segment("a", s(1), s(2), "1"), Segment("b")]

    assert offset_segments(segments, s(60)) == (Segment("a", s(61), s(62), "1"), Segment("b"))
