from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from kikoe.core.edit_history import (
    EditHistory,
    SegmentChange,
    SegmentFields,
    apply_segment_fields,
    segment_fields,
)
from kikoe.core.models import Note, Recording, RecordingSource, Segment

WHEN = datetime(2026, 10, 15, tzinfo=UTC)


def _change(
    before: str,
    after: str,
    at: float = 0.0,
    position: int = 0,
    recording_id: str = "r1",
) -> SegmentChange:
    return SegmentChange(recording_id, position, SegmentFields(before), SegmentFields(after), at)


def test_undo_then_redo_returns_the_same_step() -> None:
    history = EditHistory()
    history.record(_change("a", "b"))

    undone = history.undo()

    assert undone is not None and undone[0].before.text == "a"
    assert not history.can_undo and history.can_redo
    redone = history.redo()
    assert redone == undone
    assert history.can_undo and not history.can_redo


def test_empty_history_has_nothing_to_undo_or_redo() -> None:
    history = EditHistory()

    assert history.undo() is None
    assert history.redo() is None


def test_quick_saves_of_one_segment_merge_into_one_step() -> None:
    history = EditHistory()
    history.record(_change("a", "ab", at=0.0))
    history.record(_change("ab", "abc", at=1.0))

    step = history.undo()

    assert step is not None and (step[0].before.text, step[0].after.text) == ("a", "abc")
    assert not history.can_undo


def test_typing_back_to_the_original_text_leaves_no_step() -> None:
    history = EditHistory()
    history.record(_change("a", "ab", at=0.0))
    history.record(_change("ab", "a", at=1.0))

    assert not history.can_undo


def test_saves_after_the_merge_window_are_separate_steps() -> None:
    history = EditHistory()
    history.record(_change("a", "ab", at=0.0))
    history.record(_change("ab", "abc", at=5.0))

    history.undo()

    assert history.can_undo


def test_saves_of_different_segments_are_separate_steps() -> None:
    history = EditHistory()
    history.record(_change("a", "ab", position=0))
    history.record(_change("x", "xy", position=1))

    history.undo()

    assert history.can_undo


def test_sealing_starts_a_new_step() -> None:
    history = EditHistory()
    history.record(_change("a", "ab"))
    history.seal()
    history.record(_change("ab", "abc"))

    history.undo()

    assert history.can_undo


def test_timestamp_changes_are_never_merged() -> None:
    history = EditHistory()
    history.record(_change("a", "ab"))
    moved = SegmentChange(
        "r1", 0, SegmentFields("ab"), SegmentFields("ab", timedelta(seconds=1)), 0.5
    )
    history.record(moved)

    assert history.undo() == (moved,)
    assert history.can_undo


def test_recording_clears_redo() -> None:
    history = EditHistory()
    history.record(_change("a", "b"))
    history.undo()
    history.record(_change("a", "c"))

    assert not history.can_redo


def test_changes_recorded_together_are_undone_as_one_step() -> None:
    history = EditHistory()
    changes = (_change("a", "A"), _change("b", "B", position=1))
    history.record_all(changes)

    assert history.undo() == changes
    assert not history.can_undo
    assert history.redo() == changes


def test_typing_never_joins_a_step_recorded_together() -> None:
    history = EditHistory()
    history.record_all((_change("a", "ab", at=0.0),))
    history.record(_change("ab", "abc", at=0.5))

    history.undo()

    assert history.can_undo


def test_recording_together_drops_no_op_changes() -> None:
    history = EditHistory()
    history.record_all((_change("a", "a"),))

    assert not history.can_undo


def test_no_op_change_is_dropped() -> None:
    history = EditHistory()
    history.record(_change("a", "a"))

    assert not history.can_undo


def test_clear_forgets_everything() -> None:
    history = EditHistory()
    history.record(_change("a", "b"))
    history.record(_change("x", "y", position=1))
    history.undo()

    history.clear()

    assert not history.can_undo and not history.can_redo


def _note() -> Note:
    def recording(recording_id: str, *texts: str) -> Recording:
        segments = tuple(Segment(t, speaker_id="A") for t in texts)
        return Recording(
            recording_id, WHEN, timedelta(seconds=9), RecordingSource.IMPORTED, "P", "M", segments
        )

    return Note("n", "Note", WHEN, (recording("r1", "a", "b"), recording("r2", "c")))


def test_applying_fields_replaces_only_that_segment_and_keeps_its_speaker() -> None:
    note = _note()
    fields = SegmentFields("B", timedelta(seconds=1), timedelta(seconds=2))

    edited = apply_segment_fields(note, "r1", 1, fields)

    assert edited.recordings[0].segments == (
        Segment("a", speaker_id="A"),
        Segment("B", timedelta(seconds=1), timedelta(seconds=2), "A"),
    )
    assert edited.recordings[1] is note.recordings[1]
    assert edited.recordings[0].segments[0] is note.recordings[0].segments[0]
    assert segment_fields(edited, "r1", 1) == fields


def test_segment_fields_of_a_missing_segment_are_none() -> None:
    note = _note()
    assert segment_fields(note, "r2", 1) is None
    assert segment_fields(note, "r2", -1) is None
    assert segment_fields(note, "missing", 0) is None


def _current(**values: str) -> Callable[[str, int], SegmentFields | None]:
    return lambda recording_id, position: SegmentFields(values[f"{recording_id}{position}"])


def test_history_matches_a_note_that_holds_the_edited_values() -> None:
    history = EditHistory()
    history.record(_change("a", "b"))
    assert history.matches(_current(r10="b"))


def test_history_matches_after_undo_when_the_note_holds_the_older_value() -> None:
    history = EditHistory()
    history.record(_change("a", "b"))
    history.undo()
    assert history.matches(_current(r10="a"))
    assert not history.matches(_current(r10="b"))


def test_history_does_not_match_a_segment_replaced_behind_its_back() -> None:
    history = EditHistory()
    history.record(_change("a", "b"))
    assert not history.matches(_current(r10="c"))
    assert not history.matches(lambda _recording_id, _position: None)


def test_history_without_steps_matches_any_note() -> None:
    assert EditHistory().matches(lambda _recording_id, _position: None)


def test_history_matches_every_segment_of_a_step_recorded_together() -> None:
    history = EditHistory()
    history.record_all((_change("a", "A"), _change("b", "B", position=1)))
    assert history.matches(_current(r10="A", r11="B"))
    assert not history.matches(_current(r10="A", r11="b"))
    history.undo()
    assert history.matches(_current(r10="a", r11="b"))
    assert not history.matches(_current(r10="A", r11="b"))
