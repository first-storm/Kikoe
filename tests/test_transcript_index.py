"""Transcript indexing retains recording boundaries and shared speaker identities."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from kikoe.core.models import Note, Recording, RecordingSource, Segment, SpeakerBinding
from kikoe.core.transcript_index import TranscriptIndex


def recording(name: str, segments: tuple[Segment, ...] = ()) -> Recording:
    return Recording(
        name, datetime.now(UTC), timedelta(), RecordingSource.IMPORTED, "", "", segments
    )


def test_empty_recordings_still_have_headers_and_positions_are_bounded() -> None:
    note = Note("n", "Note", datetime.now(UTC), (recording("a"), recording("b", (Segment("B"),))))
    index = TranscriptIndex.build(note)
    assert index.count == 3
    assert [index.locate(i) for i in range(index.count)] == [(0, None), (1, None), (1, 0)]
    for position in (-1, 3):
        with pytest.raises(IndexError):
            index.locate(position)


def test_matched_labels_share_numbers_without_copying_segments() -> None:
    first = replace(
        recording("a", (Segment("A", speaker_id="0"), Segment("B", speaker_id="1"))),
        speaker_bindings=(SpeakerBinding("0", "alice", "a"), SpeakerBinding("1", "bob", "b")),
    )
    second = replace(
        recording("b", (Segment("B again", end=timedelta(seconds=2), speaker_id="other"),)),
        speaker_bindings=(SpeakerBinding("other", "bob", "c"),),
    )
    note = Note("n", "Note", datetime.now(UTC), (first, second))
    index = TranscriptIndex.build(note)
    assert index.speaker_order == {"alice": 0, "bob": 1}
    assert index.identities[1]["other"] == "bob"
    assert index.has_speakers and index.has_timestamps
    assert index.locate(4) == (1, 0)
    assert first.segments[0].speaker_id == "0"


def test_large_transcript_indexes_rows_without_expanding_text() -> None:
    segment = Segment("Long transcript " * 100)
    note = Note("n", "Note", datetime.now(UTC), (recording("a", (segment,) * 100_000),))
    index = TranscriptIndex.build(note)
    assert index.offsets == (0,)
    assert index.count == 100_001
    assert index.locate(100_000) == (0, 99_999)
    assert not index.has_speakers and not index.has_timestamps


def test_no_recordings_produces_an_empty_index() -> None:
    index = TranscriptIndex.build(Note("n", "Note", datetime.now(UTC)))
    assert index.count == 0
    assert index.offsets == ()
    with pytest.raises(IndexError):
        index.locate(0)
