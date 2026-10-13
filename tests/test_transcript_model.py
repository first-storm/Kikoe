"""Lazy GTK wrappers preserve edits, row identity and live transcript boundaries."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from kikoe.core.models import Note, Recording, RecordingSource, Segment

if TYPE_CHECKING:
    from kikoe.ui.transcript_model import TranscriptModel

pytestmark = pytest.mark.gtk


@pytest.fixture
def model() -> TranscriptModel:
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from kikoe.ui.transcript_model import TranscriptModel

    return TranscriptModel()


def note(count: int) -> Note:
    recording = Recording(
        "r",
        datetime.now(UTC),
        timedelta(),
        RecordingSource.IMPORTED,
        "",
        "",
        (Segment("Text"),) * count,
    )
    return Note("n", "Note", datetime.now(UTC), (recording,))


def test_large_note_only_wraps_requested_rows(model: TranscriptModel) -> None:
    model.set_note(note(100_000))
    assert model.get_n_items() == 100_001
    assert not model._cache
    item = model.get_item(99_999)
    assert item is not None and item.position == 99_998
    assert len(model._cache) == 1
    assert model.get_item(99_999) is item
    assert model.get_item(100_001) is None


def test_edit_refresh_keeps_item_identity_and_positions(model: TranscriptModel) -> None:
    original = note(2)
    model.set_note(original)
    item = model.get_item(1)
    changed = replace(original.recordings[0], segments=(Segment("Edited"), Segment("Text")))
    model.set_note(replace(original, recordings=(changed,)))
    assert model.get_item(1) is item
    assert item is not None and item.segment == Segment("Edited")
    assert model.find(item) == (True, 1)


def test_note_switch_clears_removed_rows_and_live_suffix(model: TranscriptModel) -> None:
    from kikoe.ui.items import TranscriptItem

    original = note(10)
    model.set_note(original)
    model.get_item(10)
    live = TranscriptItem(original.recordings[0], 2, Segment("Live"), preview=True)
    model.splice(11, 0, [live])
    assert model.get_item(11) is live
    model.set_note(note(1))
    assert model.get_n_items() == 2
    assert 10 not in model._cache
    assert model.get_item(2) is None


def test_live_splice_cannot_replace_saved_transcript_rows(model: TranscriptModel) -> None:
    model.set_note(note(2))
    with pytest.raises(IndexError):
        model.splice(1, 1, [])


def test_saved_text_refresh_retains_live_rows_and_their_prefix(model: TranscriptModel) -> None:
    from kikoe.ui.items import TranscriptItem

    original = note(2)
    model.set_note(original)
    live = TranscriptItem(original.recordings[0], 2, Segment("Live"), preview=True)
    model.splice(3, 0, [live])
    changed = replace(original.recordings[0], segments=(Segment("Edited"), Segment("Text")))
    model.set_note(replace(original, recordings=(changed,)), preserve_live=True)
    assert model.get_n_items() == 4
    assert model.get_item(3) is live
    model.splice(4, 0, [])


def test_committing_live_rows_notifies_gtk_even_when_total_count_is_unchanged(
    model: TranscriptModel,
) -> None:
    from kikoe.ui.items import TranscriptItem

    original = note(2)
    model.set_note(original)
    live = TranscriptItem(original.recordings[0], 2, Segment("Live"), preview=True)
    model.splice(3, 0, [live])
    changes: list[tuple[int, int, int]] = []

    def changed(_model: TranscriptModel, position: int, removed: int, added: int) -> None:
        changes.append((position, removed, added))

    model.connect("items-changed", changed)
    model.set_note(note(3))
    assert model.get_n_items() == 4
    assert model.get_item(3) is not live
    assert changes == [(3, 1, 1)]
