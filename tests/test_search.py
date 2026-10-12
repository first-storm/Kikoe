"""Title and transcript search is literal, Unicode-aware and independent of display."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from helpers import run_async
from kikoe.core.models import Recording, RecordingSource, Segment
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository


def test_search_finds_titles_and_transcript_without_wildcards(tmp_path: Path) -> None:
    library = Library(tmp_path / "library.sqlite3", tmp_path / "audio")
    library.migrate()
    older = library.create_note("Straße 100%_", datetime(2026, 1, 1, tzinfo=UTC))
    newer = library.add_recording(
        None,
        "会议",
        Recording(
            "r",
            datetime(2026, 2, 1, tzinfo=UTC),
            timedelta(seconds=5),
            RecordingSource.IMPORTED,
            "Cloud",
            "Model",
            (Segment("讨论 Open Source 和中文内容"),),
            None,
        ),
    )
    assert [n.note_id for n in library.search_notes("  STRASSE ")] == [older.note_id]
    assert [n.note_id for n in library.search_notes("open source")] == [newer.note_id]
    assert [n.note_id for n in library.search_notes("中文")] == [newer.note_id]
    assert [n.note_id for n in library.search_notes("%_")] == [older.note_id]
    assert library.search_notes("%' OR 1=1 --") == ()
    assert [n.note_id for n in library.search_notes(" ")] == [newer.note_id, older.note_id]
    library.rename_note(older.note_id, "New title")
    assert library.search_notes("STRASSE") == ()
    library.delete_note(newer.note_id)
    assert library.search_notes("中文") == ()


@run_async
async def test_repository_search_refreshes_after_rename(tmp_path: Path) -> None:
    repository = NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))
    await repository.load()
    note = await repository.create_note("Meeting")
    assert (await repository.search("meeting"))[0].note_id == note.note_id
    await repository.rename_note(note.note_id, "Podcast")
    assert await repository.search("meeting") == ()
    assert (await repository.search("podcast"))[0].note_id == note.note_id
