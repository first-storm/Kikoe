from datetime import UTC, datetime, timedelta

from kikoe.core.models import (
    Note,
    Recording,
    RecordingSource,
    Speaker,
)


def _note() -> Note:
    recording = Recording(
        "r1", datetime(2026, 1, 1, tzinfo=UTC), timedelta(), RecordingSource.IMPORTED, "P", "m"
    )
    return Note(
        "n1",
        "Note",
        datetime(2026, 1, 1, tzinfo=UTC),
        recordings=(recording,),
        speakers=(Speaker("s1", "Ana", 1),),
    )


def test_note_finds_recording_and_speaker_by_id() -> None:
    note = _note()

    assert note.recording("r1") is note.recordings[0]
    assert note.speaker("s1") is note.speakers[0]
    assert note.speaker_name("s1") == "Ana"


def test_note_lookups_return_none_for_unknown_ids() -> None:
    note = _note()

    assert note.recording("missing") is None
    assert note.speaker("missing") is None
    assert note.speaker(None) is None
    assert note.speaker_name("missing") is None
