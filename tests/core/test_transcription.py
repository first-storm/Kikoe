"""API limits and live revisions are domain rules, independent of widgets."""

from datetime import timedelta

from kikoe.core.models import Segment
from kikoe.core.transcript_words import TranscriptWord, group_words
from kikoe.core.transcription import (
    FileTranscriptSnapshot,
    LiveTranscript,
    SnapshotScope,
    SpeechBoundary,
    TranscriptEvent,
    scope_speakers,
)


def test_final_replaces_preview_and_duplicate_final_without_appending() -> None:
    text = LiveTranscript()
    text.apply(TranscriptEvent("1", (Segment("hel"),), False))
    text.apply(TranscriptEvent("1", (Segment("hello"),), False))
    assert text.segments == ()
    assert text.preview == (Segment("hello"),)
    text.apply(TranscriptEvent("1", (Segment("Hello."),), True))
    text.apply(TranscriptEvent("1", (Segment("Hello."),), True))
    text.apply(TranscriptEvent("1", (Segment("stale"),), False))
    assert text.segments == (Segment("Hello."),)
    assert text.preview == ()


def test_speaker_zero_is_scoped_without_merging_independent_recordings() -> None:
    source = (Segment("hello", speaker_id="0"), Segment("text"))
    first = scope_speakers(source, "recording-1")
    second = scope_speakers(source, "recording-2")
    assert first[0].speaker_id != second[0].speaker_id
    assert first[1].speaker_id is None


def test_word_grouping_preserves_speaker_changes_and_chinese_spacing() -> None:
    words = tuple(
        TranscriptWord(t, timedelta(seconds=i), timedelta(seconds=i + 1), s)
        for i, (t, s) in enumerate([("你", "0"), ("好。", "0"), ("Hello", "1"), ("there.", "1")])
    )
    assert group_words(words) == (
        Segment("你好。", timedelta(), timedelta(seconds=2), "0"),
        Segment("Hello there.", timedelta(seconds=2), timedelta(seconds=4), "1"),
    )


def test_manual_commit_requires_speech_and_flushes_after_half_second_pause() -> None:
    boundary = SpeechBoundary()
    assert not boundary.feed(timedelta(seconds=10), 0)
    assert not boundary.feed(timedelta(milliseconds=100), 1)
    assert not boundary.feed(timedelta(milliseconds=400), 0)
    assert boundary.feed(timedelta(milliseconds=100), 0)
    assert not boundary.feed(timedelta(seconds=10), 0)


def test_manual_commit_limits_unbroken_speech_to_fifteen_seconds() -> None:
    boundary = SpeechBoundary()
    assert not boundary.feed(timedelta(seconds=14), 1)
    assert boundary.feed(timedelta(seconds=1), 1)


def test_file_snapshot_scopes_final_and_preview_without_mutating_source() -> None:
    snapshot = FileTranscriptSnapshot(
        (Segment("Final", speaker_id="0"),), (Segment("Preview", speaker_id="0"),)
    )
    result = SnapshotScope("recording").apply(snapshot)
    assert result.segments[0].speaker_id == result.preview[0].speaker_id == "recording:0"
    assert snapshot.segments[0].speaker_id == "0"


def test_snapshot_scope_reuses_unchanged_segments_and_rescopes_the_rest() -> None:
    scope = SnapshotScope("r")
    first = scope.apply(FileTranscriptSnapshot((Segment("a", speaker_id="0"), Segment("c"))))
    # Equal but distinct objects, as the import service builds them for every event.
    second = scope.apply(
        FileTranscriptSnapshot(
            (Segment("a", speaker_id="0"), Segment("b", speaker_id="1"), Segment("c"))
        )
    )
    assert second.segments[0] is first.segments[0]
    assert [s.speaker_id for s in second.segments] == ["r:0", "r:1", None]
    third = scope.apply(FileTranscriptSnapshot((Segment("x", speaker_id="0"),)))
    assert third.segments == (Segment("x", speaker_id="r:0"),)
