import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.models import DisplayOptions, Recording, RecordingSource, Segment
from kikoe.storage.library import SCHEMA_VERSION, Library, LibraryError, NoteNotFoundError
from kikoe.storage.notes import NoteRepository

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
        audio = library.store_audio(original, recording_id)
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
        library.store_audio(tmp_path / "missing.wav", "r1")


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


def test_version_one_library_migrates_without_changing_existing_transcript(tmp_path: Path) -> None:
    from kikoe.core.models import TranscriptionState
    from kikoe.storage.library import _MIGRATIONS

    database = tmp_path / "library.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(_MIGRATIONS[0])
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
