"""Recognition IDs can change while people, colors and manual decisions remain stable."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from kikoe.core.models import Note, Recording, RecordingSource, Segment, Speaker, SpeakerBinding
from kikoe.core.speakers import (
    binding_choices,
    preserved_identities,
    resolved_note,
    sample_range,
    sample_segment,
    speaker_numbers,
    unresolved_recordings,
)


def segment(person: str, start: float, end: float) -> Segment:
    return Segment("Speech", timedelta(seconds=start), timedelta(seconds=end), person)


def recording(segments: tuple[Segment, ...]) -> Recording:
    return Recording(
        "recording",
        datetime.now(UTC),
        timedelta(seconds=60),
        RecordingSource.IMPORTED,
        "Provider",
        "model",
        segments,
    )


def test_number_changes_preserve_people_by_time_not_order() -> None:
    old = replace(
        recording((segment("0", 0, 5), segment("1", 5, 10))),
        speaker_bindings=(
            SpeakerBinding("0", "alice", "alice", True),
            SpeakerBinding("1", "bob", "bob", True),
        ),
    )
    assert preserved_identities(old, (segment("new1", 5, 10), segment("new0", 0, 5))) == {
        "new0": "alice",
        "new1": "bob",
    }


@pytest.mark.parametrize(
    "new",
    [
        (segment("merged", 0, 10),),
        (Segment("Untimed", speaker_id="0"),),
        (segment("0", 12, 16),),
        (segment("0", 0, 5), Segment("Untimed", speaker_id="0")),
    ],
)
def test_ambiguous_or_untimed_speakers_are_not_guessed(new: tuple[Segment, ...]) -> None:
    assert (
        preserved_identities(recording((segment("alice", 0, 5), segment("bob", 5, 10))), new) == {}
    )


def test_duplicate_intervals_cannot_inflate_evidence() -> None:
    old = recording((segment("alice", 0, 4), segment("alice", 0, 4)))
    assert preserved_identities(old, (segment("new", 0, 10),)) == {}


def test_eighty_percent_coverage_is_required() -> None:
    old = recording((segment("alice", 0, 8),))
    assert preserved_identities(old, (segment("new", 0, 10),)) == {"new": "alice"}
    assert preserved_identities(old, (segment("new", 0, 10.1),)) == {}


def test_resolution_and_rematching_do_not_destroy_source_labels() -> None:
    original = replace(
        recording((segment("local", 0, 5),)),
        speaker_bindings=(SpeakerBinding("local", "alice", "independent", True),),
    )
    note = Note("note", "Meeting", datetime.now(UTC), (original,))
    assert resolved_note(note).recordings[0].segments[0].speaker_id == "alice"
    assert original.segments[0].speaker_id == "local"
    assert binding_choices(original, {"local": None})[0].speaker_id == "independent"
    assert binding_choices(original, {"local": "bob"})[0].speaker_id == "bob"


def test_explicit_keep_as_new_clears_unmatched_status() -> None:
    original = replace(
        recording((segment("local", 0, 5),)),
        speaker_bindings=(SpeakerBinding("local", "person", "person"),),
    )
    note = Note("note", "Meeting", datetime.now(UTC), (original,))
    assert unresolved_recordings(note) == (original,)
    confirmed = replace(original, speaker_bindings=binding_choices(original, {"local": None}))
    assert unresolved_recordings(replace(note, recordings=(confirmed,))) == ()


def test_sample_uses_next_timestamp_and_caps_ten_seconds() -> None:
    turn = Segment("Quote", timedelta(seconds=2), speaker_id="0")
    audio = recording((turn, segment("1", 8, 12)))
    assert sample_segment(audio, "0") == turn
    assert sample_range(audio, turn) == (timedelta(seconds=2), timedelta(seconds=8))
    assert sample_range(recording((segment("0", 2, 50),)), segment("0", 2, 50)) == (
        timedelta(seconds=2),
        timedelta(seconds=12),
    )


def test_untimed_sample_remains_quotable_but_not_playable() -> None:
    turn = Segment("Quote", speaker_id="0")
    audio = recording((turn,))
    assert sample_segment(audio, "0") == turn
    assert sample_range(audio, turn) is None
    assert sample_range(audio, segment("0", 70, 75)) is None


def test_named_old_people_remain_available_when_their_labels_are_replaced() -> None:
    from kikoe.core.models import Speaker
    from kikoe.core.speakers import matching_candidates

    current = replace(
        recording((segment("new", 0, 5),)),
        speaker_bindings=(SpeakerBinding("new", "new-person", "new-person"),),
    )
    alice = Speaker("alice", "Alice", 1)
    note = Note(
        "note", "Meeting", datetime.now(UTC), (current,), (alice, Speaker("new-person", "", 2))
    )
    assert matching_candidates(note) == (alice,)


def test_live_preview_numbering_accounts_for_retained_old_people() -> None:
    from kikoe.core.models import Speaker
    from kikoe.core.speakers import live_speaker_numbers

    note = Note(
        "note",
        "Meeting",
        datetime.now(UTC),
        speakers=(Speaker("alice", "Alice", 1), Speaker("retired", "Old person", 6)),
    )
    numbers = live_speaker_numbers(
        note, (segment("live0", 0, 2), segment("live1", 2, 4), segment("live0", 4, 6))
    )
    assert numbers["alice"] == 1
    assert numbers["live0"] == 7
    assert numbers["live1"] == 8


def test_speakers_without_ordinals_are_numbered_by_first_appearance() -> None:
    segments = (*(segment(s, 0, 1) for s in ("b", "a", "b", "c")), Segment("no speaker"))
    note = Note("note", "Meeting", datetime.now(UTC), (recording(segments),))

    assert speaker_numbers(note) == {"b": 1, "a": 2, "c": 3}


def test_stable_ordinals_win_and_legacy_zero_ordinals_are_not_shown() -> None:
    segments = (segment("legacy", 0, 1), segment("alice", 1, 2))
    note = Note(
        "note",
        "Meeting",
        datetime.now(UTC),
        (recording(segments),),
        (Speaker("legacy", "", 0), Speaker("alice", "Alice", 5), Speaker("absent", "", 3)),
    )

    assert speaker_numbers(note) == {"legacy": 1, "alice": 5, "absent": 3}


def test_speaker_numbers_follow_the_resolved_identity() -> None:
    bound = replace(
        recording((segment("0", 0, 1),)),
        speaker_bindings=(SpeakerBinding("0", "alice", "0", True),),
    )
    note = Note("note", "Meeting", datetime.now(UTC), (bound,))

    assert speaker_numbers(note) == {"alice": 1}
