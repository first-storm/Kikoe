import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.models import DisplayOptions, Recording, RecordingSource, Segment
from kikoe.storage.errors import LibraryError, NoteNotFoundError
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository
from kikoe.storage.schema import SCHEMA_VERSION

WHEN = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)


@pytest.fixture
def library(tmp_path: Path) -> Library:
    library = Library(tmp_path / "library.sqlite3", tmp_path / "audio")
    library.migrate()
    return library


def recording(
    library: Library | None = None,
    tmp_path: Path | None = None,
    seconds: int = 60,
    segments: tuple[Segment, ...] = (),
    source: RecordingSource = RecordingSource.IMPORTED,
) -> Recording:
    recording_id = uuid.uuid4().hex
    audio = None
    if library is not None and tmp_path is not None:
        original = tmp_path / "Original.MP3"
        original.write_bytes(b"audio")
        audio = library.audio.store(original, recording_id)
    return Recording(
        recording_id,
        WHEN,
        timedelta(seconds=seconds),
        source,
        "Groq",
        "whisper-large-v3",
        segments,
        audio,
    )


def test_note_with_recording_survives_reopening(tmp_path: Path, library: Library) -> None:
    segments = (
        Segment("Hello.", timedelta(seconds=1.5), timedelta(seconds=2.25)),
        Segment("Second line.", speaker_id="1"),
    )
    saved = library.add_recording(None, "Interview", recording(library, tmp_path, 90, segments))

    reopened = Library(tmp_path / "library.sqlite3", tmp_path / "audio")
    reopened.migrate()

    assert reopened.get_note(saved.note_id) == saved
    assert saved.title == "Interview"
    assert saved.recordings[0].segments == segments
    assert saved.recordings[0].audio_path == tmp_path / "audio" / (
        saved.recordings[0].recording_id + ".mp3"
    )


def test_appended_recordings_keep_their_order(library: Library) -> None:
    note = library.create_note("Meeting", WHEN)
    first = recording(seconds=10)
    second = recording(seconds=20)

    library.add_recording(note.note_id, "ignored", first)
    result = library.add_recording(note.note_id, "ignored", second)

    assert [r.recording_id for r in result.recordings] == [
        first.recording_id,
        second.recording_id,
    ]
    assert result.title == "Meeting"
    assert result.duration == timedelta(seconds=30)


def test_appending_to_a_deleted_note_fails(library: Library) -> None:
    with pytest.raises(NoteNotFoundError):
        library.add_recording("missing", "x", recording())


def test_summaries_add_up_recordings_newest_first(library: Library) -> None:
    older = library.create_note("Empty", WHEN - timedelta(days=1))
    newer = library.add_recording(None, "Two", recording(seconds=30))
    library.add_recording(newer.note_id, "", recording(seconds=45, source=RecordingSource.RECORDED))

    summaries = library.list_notes()

    assert [s.note_id for s in summaries] == [newer.note_id, older.note_id]
    assert summaries[0].recording_count == 2
    assert summaries[0].duration == timedelta(seconds=75)
    assert summaries[0].source is RecordingSource.IMPORTED
    assert summaries[1].recording_count == 0
    assert summaries[1].source is None


def test_rename_and_display_options_persist(library: Library) -> None:
    note = library.create_note("Old", WHEN)

    library.rename_note(note.note_id, "New")
    library.set_display_options(note.note_id, DisplayOptions(show_timestamps=False))

    stored = library.get_note(note.note_id)
    assert stored.title == "New"
    assert stored.display == DisplayOptions(show_timestamps=False, show_speakers=True)


def test_delete_note_removes_rows_and_audio(tmp_path: Path, library: Library) -> None:
    note = library.add_recording(
        None, "Memo", recording(library, tmp_path, segments=(Segment("x"),))
    )
    audio = note.recordings[0].audio_path
    assert audio is not None and audio.is_file()

    library.delete_note(note.note_id)

    assert not audio.exists()
    assert library.list_notes() == ()
    with sqlite3.connect(tmp_path / "library.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM segments").fetchone() == (0,)
    with pytest.raises(NoteNotFoundError):
        library.get_note(note.note_id)


def test_delete_recording_removes_audio_segments_and_keeps_note(
    tmp_path: Path, library: Library
) -> None:
    note = library.add_recording(
        None, "Memo", recording(library, tmp_path, segments=(Segment("first"),))
    )
    note = library.add_recording(
        note.note_id, "", recording(library, tmp_path, segments=(Segment("second"),))
    )
    first, second = note.recordings
    assert first.audio_path is not None and first.audio_path.is_file()
    assert second.audio_path is not None and second.audio_path.is_file()

    result = library.delete_recording(note.note_id, first.recording_id)

    assert [r.recording_id for r in result.recordings] == [second.recording_id]
    assert not first.audio_path.exists()
    assert second.audio_path.is_file()
    assert library.list_notes()[0].recording_count == 1
    with sqlite3.connect(tmp_path / "library.sqlite3") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM segments WHERE recording_id = ?", (first.recording_id,)
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT COUNT(*) FROM segments WHERE recording_id = ?", (second.recording_id,)
        ).fetchone() == (1,)


def test_delete_recording_last_one_keeps_empty_note(tmp_path: Path, library: Library) -> None:
    note = library.add_recording(
        None, "Memo", recording(library, tmp_path, segments=(Segment("x"),))
    )

    result = library.delete_recording(note.note_id, note.recordings[0].recording_id)

    assert result.recordings == ()
    assert [s.note_id for s in library.list_notes()] == [note.note_id]


def test_delete_missing_recording_raises(library: Library) -> None:
    note = library.create_note("Memo", WHEN)

    with pytest.raises(LibraryError):
        library.delete_recording(note.note_id, "missing")


def test_newer_database_is_rejected_and_left_alone(tmp_path: Path) -> None:
    database = tmp_path / "library.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    with pytest.raises(LibraryError, match="newer version"):
        Library(database, tmp_path / "audio").migrate()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION + 1,)


def test_migrating_twice_is_harmless(library: Library) -> None:
    library.migrate()

    assert library.list_notes() == ()


def test_copying_a_missing_file_is_a_library_error(tmp_path: Path, library: Library) -> None:
    with pytest.raises(LibraryError, match="Could not copy"):
        library.audio.store(tmp_path / "missing.wav", "r1")


def test_stored_audio_is_synced_through_a_writable_handle(
    tmp_path: Path, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Windows raises EBADF when fsync gets a descriptor opened read-only; a zero-byte
    # write fails the same way on a read-only descriptor everywhere.
    synced: list[int] = []

    def fsync(fd: int) -> None:
        os.write(fd, b"")
        synced.append(fd)

    source = tmp_path / "original.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(os, "fsync", fsync)

    stored = library.audio.store(source, "r1")

    assert len(synced) == 1
    assert stored.read_bytes() == b"audio"


def test_failed_copy_leaves_no_partial_file(
    tmp_path: Path, library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_fd: int) -> None:
        raise OSError("disk full")

    source = tmp_path / "original.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(os, "fsync", fail)

    with pytest.raises(LibraryError, match="Could not copy"):
        library.audio.store(source, "r1")

    assert not (tmp_path / "audio" / "r1.wav").exists()


@run_async
async def test_repository_notifies_with_the_changed_note(tmp_path: Path) -> None:
    repository = NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))
    changes: list[str | None] = []
    repository.connect_changed(changes.append)

    await repository.load()
    note = await repository.create_note("Draft")
    await repository.rename_note(note.note_id, "  Final  ")

    assert changes == [None, note.note_id, note.note_id]
    assert [s.title for s in repository.notes] == ["Final"]


@run_async
async def test_repository_delete_recording_notifies(tmp_path: Path, library: Library) -> None:
    repository = NoteRepository(library)
    changes: list[str | None] = []
    repository.connect_changed(changes.append)

    await repository.load()
    saved = library.add_recording(None, "Memo", recording(library, tmp_path))
    note = await repository.delete_recording(saved.note_id, saved.recordings[0].recording_id)

    assert note.recordings == ()
    assert changes[-1] == saved.note_id
    assert repository.notes[0].recording_count == 0


def test_version_one_library_migrates_without_changing_existing_transcript(tmp_path: Path) -> None:
    from kikoe.core.models import TranscriptionState
    from kikoe.storage.schema import MIGRATIONS

    database = tmp_path / "library.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(MIGRATIONS[0])
        connection.execute("PRAGMA user_version = 1")
        connection.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 1, 1)", ("n", "Old", WHEN.isoformat())
        )
        connection.execute(
            "INSERT INTO recordings VALUES (?, ?, 0, ?, 1000, ?, ?, ?, NULL)",
            ("r", "n", WHEN.isoformat(), "imported", "Groq", "whisper"),
        )
        connection.execute(
            "INSERT INTO segments VALUES (?, 0, 0, 1000, NULL, ?)", ("r", "Existing transcript")
        )
    migrated = Library(database, tmp_path / "audio")
    migrated.migrate()
    note = migrated.get_note("n")
    assert note.recordings[0].transcription_state is TranscriptionState.READY
    assert note.recordings[0].segments == (
        Segment("Existing transcript", timedelta(), timedelta(seconds=1)),
    )
    assert note.title == "Old"


def test_incomplete_transcript_survives_retry_failure_and_successful_empty_result(
    library: Library,
) -> None:
    from kikoe.core.models import TranscriptionState

    note = library.add_recording(None, "Test", recording(segments=(Segment("Confirmed"),)))
    id = note.recordings[0].recording_id
    library.update_transcription(note.note_id, id, TranscriptionState.PENDING)
    failed = library.update_transcription(note.note_id, id, TranscriptionState.FAILED)
    assert failed.recordings[0].segments == (Segment("Confirmed"),)
    changed = library.update_transcription(
        note.note_id, id, TranscriptionState.FAILED, segments=(Segment("More confirmed"),)
    )
    assert changed.recordings[0].segments == (Segment("More confirmed"),)
    empty = library.update_transcription(note.note_id, id, TranscriptionState.READY, segments=())
    assert empty.recordings[0].segments == ()


def test_speakers_can_merge_and_separate_after_reopening(library: Library, tmp_path: Path) -> None:
    from kikoe.core.speakers import resolved_note, unresolved_recordings

    first = recording(segments=(Segment("Alice", speaker_id="first:0"),))
    note = library.add_recording(None, "Meeting", first)
    alice = note.speakers[0]
    library.rename_speaker(note.note_id, alice.speaker_id, "Alice")
    second = recording(
        segments=(
            Segment("Again", speaker_id="second:0"),
            Segment("Also Alice", speaker_id="second:1"),
        )
    )
    note = library.add_recording(note.note_id, "ignored", second)
    assert unresolved_recordings(note) == (note.recordings[1],)
    originals = tuple(b.original_id for b in note.recordings[1].speaker_bindings)
    note = library.match_speakers(
        note.note_id,
        second.recording_id,
        {"second:0": alice.speaker_id, "second:1": alice.speaker_id},
    )
    assert not unresolved_recordings(note)
    assert {s.speaker_id for s in resolved_note(note).recordings[1].segments} == {alice.speaker_id}
    reopened = Library(tmp_path / "library.sqlite3", tmp_path / "audio")
    reopened.migrate()
    assert reopened.get_note(note.note_id) == note
    note = reopened.match_speakers(note.note_id, second.recording_id, {"second:1": None})
    assert resolved_note(note).recordings[1].segments[1].speaker_id == originals[1]
    assert note.speakers[0].name == "Alice"
    assert note.speakers[0].ordinal == alice.ordinal


def test_invalid_matching_rolls_back_all_choices(library: Library) -> None:
    note = library.add_recording(
        None, "Meeting", recording(segments=(Segment("Hi", speaker_id="0"),))
    )
    before = library.get_note(note.note_id)
    with pytest.raises(LibraryError):
        library.match_speakers(
            note.note_id,
            note.recordings[0].recording_id,
            {"0": note.speakers[0].speaker_id, "missing": None},
        )
    assert library.get_note(note.note_id) == before
    with pytest.raises(LibraryError):
        library.rename_speaker(note.note_id, note.speakers[0].speaker_id, "   ")


def test_replacement_keeps_named_identity_and_other_recording(library: Library) -> None:
    from kikoe.core.models import TranscriptionState
    from kikoe.core.speakers import resolved_note

    first = recording(segments=(Segment("Old", timedelta(), timedelta(seconds=5), "0"),))
    note = library.add_recording(None, "Meeting", first)
    person = note.speakers[0]
    library.rename_speaker(note.note_id, person.speaker_id, "Alice")
    note = library.add_recording(note.note_id, "ignored", recording(segments=(Segment("Other"),)))
    other = note.recordings[1]
    updated = library.update_transcription(
        note.note_id,
        first.recording_id,
        TranscriptionState.READY,
        segments=(Segment("New", timedelta(), timedelta(seconds=5), "99"),),
    )
    assert resolved_note(updated).recordings[0].segments[0].speaker_id == person.speaker_id
    assert updated.speakers[0].name == "Alice"
    assert updated.speakers[0].ordinal == person.ordinal
    assert updated.recordings[1] == other


def test_v2_library_migrates_existing_speaker_names(tmp_path: Path) -> None:
    from kikoe.storage.schema import MIGRATIONS

    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        for migration in MIGRATIONS[:2]:
            connection.executescript(migration)
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            "INSERT INTO notes VALUES ('note', 'Meeting', ?, 1, 1)", (WHEN.isoformat(),)
        )
        connection.execute("INSERT INTO speakers VALUES ('note', 'local', 'Alice')")
        connection.execute(
            "INSERT INTO recordings VALUES ('r', 'note', 0, ?, 5000, "
            "'imported', 'P', 'M', NULL, 'ready')",
            (WHEN.isoformat(),),
        )
        connection.execute("INSERT INTO segments VALUES ('r', 0, 0, 5000, 'local', 'Old text')")
    old = Library(database, tmp_path / "audio")
    old.migrate()
    note = old.get_note("note")
    assert note.speakers[0].name == "Alice"
    assert note.speakers[0].ordinal == 1
    assert note.recordings[0].speaker_bindings[0].speaker_id == "local"


def test_retranscription_does_not_confirm_an_unmatched_person(library: Library) -> None:
    from kikoe.core.models import TranscriptionState
    from kikoe.core.speakers import unresolved_recordings

    note = library.add_recording(
        None, "Meeting", recording(segments=(Segment("First", speaker_id="first"),))
    )
    second = recording(segments=(Segment("Second", timedelta(), timedelta(seconds=4), "second"),))
    note = library.add_recording(note.note_id, "", second)
    updated = library.update_transcription(
        note.note_id,
        second.recording_id,
        TranscriptionState.READY,
        segments=(Segment("Updated", timedelta(), timedelta(seconds=4), "renumbered"),),
    )
    assert unresolved_recordings(updated) == (updated.recordings[1],)
    assert (
        updated.recordings[1].speaker_bindings[0].speaker_id
        == note.recordings[1].speaker_bindings[0].speaker_id
    )


def test_replacement_transaction_rolls_back_after_deleting_old_text(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kikoe.core.models import TranscriptionState

    note = library.add_recording(
        None, "Meeting", recording(segments=(Segment("Old", speaker_id="0"),))
    )
    library.rename_speaker(note.note_id, note.speakers[0].speaker_id, "Alice")
    before = library.get_note(note.note_id)

    def fail_insert(*_args: object) -> None:
        raise LibraryError("Disk full")

    monkeypatch.setattr(library, "_insert_segments", fail_insert)
    with pytest.raises(LibraryError, match="Disk full"):
        library.update_transcription(
            note.note_id,
            note.recordings[0].recording_id,
            TranscriptionState.READY,
            segments=(Segment("New"),),
            provider_name="New provider",
            model_id="new model",
        )
    assert library.get_note(note.note_id) == before


def test_update_segment_edits_text_and_times(library: Library) -> None:
    note = library.add_recording(
        None,
        "Meeting",
        recording(
            segments=(
                Segment("One", timedelta(seconds=1), timedelta(seconds=3)),
                Segment("Two", timedelta(seconds=5), timedelta(seconds=7)),
            )
        ),
    )
    recording_id = note.recordings[0].recording_id

    updated = library.update_segment(
        note.note_id,
        recording_id,
        1,
        text="Two edited",
        start_ms=6000,
        end_ms=9000,
    )

    assert updated.recordings[0].segments[1] == Segment(
        "Two edited", timedelta(seconds=6), timedelta(seconds=9)
    )
    assert updated.recordings[0].segments[0] == Segment(
        "One", timedelta(seconds=1), timedelta(seconds=3)
    )


def test_update_segment_can_clear_timestamps(library: Library) -> None:
    note = library.add_recording(
        None,
        "Meeting",
        recording(segments=(Segment("One", timedelta(seconds=1), timedelta(seconds=3)),)),
    )
    recording_id = note.recordings[0].recording_id

    updated = library.update_segment(
        note.note_id, recording_id, 0, text="One", start_ms=None, end_ms=None
    )

    assert updated.recordings[0].segments == (Segment("One"),)


def test_update_segment_rejects_bad_input_and_missing_rows(library: Library) -> None:
    note = library.add_recording(
        None,
        "Meeting",
        recording(segments=(Segment("One", timedelta(seconds=1), timedelta(seconds=3)),)),
    )
    recording_id = note.recordings[0].recording_id
    before = library.get_note(note.note_id)
    with pytest.raises(LibraryError):
        library.update_segment(
            note.note_id, recording_id, 0, text="   ", start_ms=1000, end_ms=3000
        )
    with pytest.raises(LibraryError):
        library.update_segment(
            note.note_id, recording_id, 0, text="One", start_ms=3000, end_ms=1000
        )
    with pytest.raises(LibraryError):
        library.update_segment(
            note.note_id, recording_id, 9, text="One", start_ms=1000, end_ms=3000
        )
    with pytest.raises(LibraryError):
        library.update_segment(
            note.note_id, "missing-recording", 0, text="One", start_ms=None, end_ms=None
        )
    with pytest.raises(NoteNotFoundError):
        library.update_segment("missing-note", "missing", 0, text="One", start_ms=None, end_ms=None)
    assert library.get_note(note.note_id) == before
