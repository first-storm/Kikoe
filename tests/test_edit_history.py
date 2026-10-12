from datetime import timedelta

from kikoe.core.edit_history import EditHistory, SegmentChange, SegmentFields


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

    assert undone is not None and undone.before.text == "a"
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

    assert step is not None and (step.before.text, step.after.text) == ("a", "abc")
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

    assert history.undo() == moved
    assert history.can_undo


def test_recording_clears_redo() -> None:
    history = EditHistory()
    history.record(_change("a", "b"))
    history.undo()
    history.record(_change("a", "c"))

    assert not history.can_redo


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
