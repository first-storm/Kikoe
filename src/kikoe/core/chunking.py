"""Splitting long audio into parts small enough to upload, cutting inside silences."""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import timedelta

from kikoe.core.models import Segment


@dataclass(frozen=True, slots=True)
class TimeSpan:
    start: timedelta
    end: timedelta

    @property
    def middle(self) -> timedelta:
        return self.start + (self.end - self.start) / 2


def plan_chunks(
    duration: timedelta, silences: Iterable[TimeSpan], max_length: timedelta
) -> tuple[TimeSpan, ...]:
    """Cover ``duration`` with parts no longer than ``max_length``.

    Each cut goes in the middle of the last silence that keeps the part short enough, so
    words are not split; without such a silence the part is cut at ``max_length``.
    """
    if max_length <= timedelta():
        raise ValueError("max_length must be positive")
    cut_points = sorted(silence.middle for silence in silences)
    chunks: list[TimeSpan] = []
    start = timedelta()
    while duration - start > max_length:
        limit = start + max_length
        candidates = [point for point in cut_points if start < point <= limit]
        cut = candidates[-1] if candidates else limit
        chunks.append(TimeSpan(start, cut))
        start = cut
    chunks.append(TimeSpan(start, duration))
    return tuple(chunks)


def offset_segments(segments: Iterable[Segment], offset: timedelta) -> tuple[Segment, ...]:
    """Shift times of a part's transcript to where the part starts in the whole recording."""
    return tuple(
        replace(
            segment,
            start=None if segment.start is None else segment.start + offset,
            end=None if segment.end is None else segment.end + offset,
        )
        for segment in segments
    )
