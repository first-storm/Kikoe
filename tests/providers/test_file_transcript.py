"""Verify the shared stream accumulator and the loop that drives it."""

from collections.abc import AsyncIterator

import pytest

from kikoe.core.models import Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.providers.base import FileTranscript, TranscriptionError, consume_file_stream
from tests.helpers import run_async


class FakeTranscript(FileTranscript[int]):
    """Complete on the third event so the loop's stop condition is observable."""

    def __init__(self) -> None:
        self.seen: list[int] = []

    def apply(self, event: int) -> bool:
        self.seen.append(event)
        if event == 3:
            self.commit((Segment("final"),))
            return True
        return event == 2

    def committed(self) -> tuple[Segment, ...]:
        return (Segment("partial"),)

    def preview(self) -> tuple[Segment, ...]:
        return (Segment("live"),)


async def events(values: list[int]) -> AsyncIterator[int]:
    for value in values:
        yield value


def test_result_before_completion_is_a_retryable_failure() -> None:
    with pytest.raises(TranscriptionError, match="before it was complete") as failure:
        FakeTranscript().result()
    assert failure.value.retryable


def test_snapshot_combines_committed_segments_with_preview() -> None:
    assert FakeTranscript().snapshot == FileTranscriptSnapshot(
        (Segment("partial"),), (Segment("live"),)
    )


def test_snapshot_ignores_preview_after_completion() -> None:
    transcript = FakeTranscript()
    transcript.commit((Segment("done"),))
    assert transcript.finished
    assert transcript.snapshot == FileTranscriptSnapshot((Segment("done"),))


@run_async
async def test_consume_file_stream_stops_at_completion() -> None:
    transcript = FakeTranscript()
    updates: list[FileTranscriptSnapshot] = []
    result = await consume_file_stream(events([1, 2, 3, 4]), transcript, updates.append)
    assert transcript.seen == [1, 2, 3]
    assert updates == [
        FileTranscriptSnapshot((Segment("partial"),), (Segment("live"),)),
        FileTranscriptSnapshot((Segment("final"),)),
    ]
    assert result == (Segment("final"),)


@run_async
async def test_consume_file_stream_requires_a_completion_event() -> None:
    with pytest.raises(TranscriptionError, match="before it was complete") as failure:
        await consume_file_stream(events([1, 2]), FakeTranscript())
    assert failure.value.retryable
