from dataclasses import replace
from datetime import UTC, datetime, timedelta

from kikoe.core.models import Note, Recording, RecordingSource, Segment, Speaker
from kikoe.core.transcript_rows import (
    OverlayPlan,
    plan_overlay,
    same_structure,
    transcript_columns,
    without_recording,
)


def recording(recording_id: str, *segments: Segment) -> Recording:
    return Recording(
        recording_id,
        datetime(2026, 1, 1, tzinfo=UTC),
        timedelta(seconds=5),
        RecordingSource.IMPORTED,
        "OpenAI",
        "whisper-1",
        segments,
    )


def note(*recordings: Recording, speakers: tuple[Speaker, ...] = ()) -> Note:
    return Note("n", "Meeting", datetime(2026, 1, 1, tzinfo=UTC), recordings, speakers)


def test_columns_report_times_and_speakers_independently() -> None:
    assert transcript_columns([Segment("plain")]) == (False, False)
    assert transcript_columns([Segment("t", timedelta(seconds=1))]) == (True, False)
    assert transcript_columns([Segment("end only", end=timedelta(seconds=1))]) == (True, False)
    assert transcript_columns([Segment("s", speaker_id="a")]) == (False, True)
    assert transcript_columns([]) == (False, False)


def test_editing_text_or_times_keeps_the_structure() -> None:
    before = note(recording("r", Segment("Hello", timedelta(seconds=1), speaker_id="a")))
    after = note(recording("r", Segment("Hi", timedelta(seconds=2), speaker_id="a")))
    assert same_structure(before, after)


def test_changing_a_speaker_or_a_segment_count_changes_the_structure() -> None:
    before = note(recording("r", Segment("Hello", speaker_id="a")))
    other_speaker = note(recording("r", Segment("Hello", speaker_id="b")))
    extra_segment = note(recording("r", Segment("Hello", speaker_id="a"), Segment("More")))
    assert not same_structure(before, other_speaker)
    assert not same_structure(before, extra_segment)


def test_adding_a_recording_or_a_person_changes_the_structure() -> None:
    before = note(recording("r"))
    assert not same_structure(before, note(recording("r"), recording("s")))
    assert not same_structure(before, replace(before, speakers=(Speaker("a", "Ann", 1),)))


def test_a_new_recording_is_appended_after_the_existing_rows() -> None:
    saved = note(recording("a", Segment("1"), Segment("2")), recording("b", Segment("3")))
    assert plan_overlay(saved, None) == OverlayPlan(number=3, first_row=5, replaced_rows=0)


def test_a_replacement_covers_the_header_and_segments_of_its_target() -> None:
    saved = note(recording("a", Segment("1"), Segment("2")), recording("b", Segment("3")))
    assert plan_overlay(saved, "b") == OverlayPlan(number=2, first_row=3, replaced_rows=2)
    assert plan_overlay(saved, "a") == OverlayPlan(number=1, first_row=0, replaced_rows=3)


def test_an_unknown_target_is_treated_as_an_append() -> None:
    saved = note(recording("a", Segment("1")))
    assert plan_overlay(saved, "missing") == OverlayPlan(number=2, first_row=2, replaced_rows=0)


def test_without_recording_drops_only_the_target() -> None:
    saved = note(recording("a"), recording("b"), recording("c"))
    assert [r.recording_id for r in without_recording(saved, "b").recordings] == ["a", "c"]
    assert without_recording(saved, None) == saved
