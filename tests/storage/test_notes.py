"""The repository reloads summaries only after changes that can alter them."""

import asyncio
import shutil
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO

import pytest

from kikoe.core.models import (
    DisplayOptions,
    Note,
    NoteSummary,
    Recording,
    RecordingSource,
    Segment,
    TranscriptionState,
)
from kikoe.storage.library import SegmentUpdate
from kikoe.storage.notes import ContentChanged, NoteEvent, NoteRepository, SummariesChanged
from tests.helpers import run_async


def recording(recording_id: str = "recording") -> Recording:
    return Recording(
        recording_id,
        datetime.now(UTC),
        timedelta(seconds=5),
        RecordingSource.IMPORTED,
        "Cloud",
        "Model",
        (Segment("Hello", speaker_id="A"),),
    )


async def note_with_events(notes: NoteRepository) -> tuple[Note, list[NoteEvent]]:
    await notes.load()
    note = await notes.add_recording(None, "Meeting", recording())
    events: list[NoteEvent] = []
    notes.connect_changed(events.append)
    return note, events


@run_async
async def test_loading_and_creating_reload_summaries(notes: NoteRepository) -> None:
    events: list[NoteEvent] = []
    notes.connect_changed(events.append)
    await notes.load()
    note = await notes.create_note("Draft")
    assert [s.title for s in notes.notes] == ["Draft"]
    assert events == [SummariesChanged(None), SummariesChanged(note.note_id)]


@run_async
async def test_renaming_a_note_reloads_its_summary(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    await notes.rename_note(note.note_id, " Standup ")
    assert [s.title for s in notes.notes] == ["Standup"]
    assert events == [SummariesChanged(note.note_id)]


@run_async
async def test_adding_and_deleting_recordings_reload_summaries(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    await notes.add_recording(note.note_id, "", recording("second"))
    assert notes.notes[0].recording_count == 2
    await notes.delete_recording(note.note_id, "second")
    assert notes.notes[0].recording_count == 1
    assert events == [SummariesChanged(note.note_id), SummariesChanged(note.note_id)]


@run_async
async def test_content_changes_keep_summaries_and_name_the_note(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    summaries = notes.notes
    speaker = note.speakers[0].speaker_id
    await notes.set_display_options(note.note_id, DisplayOptions(show_timestamps=False))
    await notes.rename_speaker(note.note_id, speaker, "Ada")
    await notes.match_speakers(note.note_id, "recording", {})
    await notes.update_transcription(
        note.note_id, "recording", TranscriptionState.READY, segments=(Segment("Again"),)
    )
    assert notes.notes is summaries
    assert events == [ContentChanged(note.note_id)] * 4
    saved = await notes.get_note(note.note_id)
    assert saved.display == DisplayOptions(show_timestamps=False)
    assert saved.recordings[0].segments == (Segment("Again"),)


@run_async
async def test_segment_edits_are_saved_without_notifying(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    await notes.update_segments(note.note_id, (SegmentUpdate("recording", 0, "Edited"),))
    assert events == []
    saved = await notes.get_note(note.note_id)
    assert saved.recordings[0].segments[0].text == "Edited"


@run_async
async def test_cancelled_audio_copy_leaves_no_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: NoteRepository
) -> None:
    source = tmp_path / "talk.wav"
    source.write_bytes(b"original")
    copying, release = threading.Event(), threading.Event()
    copy = shutil.copyfileobj

    def held_copy(origin: BinaryIO, destination: BinaryIO) -> None:
        copying.set()
        release.wait()
        copy(origin, destination)

    monkeypatch.setattr(shutil, "copyfileobj", held_copy)
    task = asyncio.create_task(notes.store_audio(source, "recording"))
    await asyncio.to_thread(copying.wait)
    task.cancel()
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert list((tmp_path / "audio").iterdir()) == []


@run_async
async def test_a_failed_summary_reload_does_not_fail_the_saved_change(
    notes: NoteRepository, tmp_path: Path
) -> None:
    await notes.load()
    library = notes._library
    reload = library.list_notes

    def locked() -> tuple[NoteSummary, ...]:
        other = sqlite3.connect(tmp_path / "library.sqlite3", isolation_level=None)
        other.execute("BEGIN EXCLUSIVE")
        try:
            return reload()
        finally:
            other.close()

    library.list_notes = locked  # pyright: ignore[reportAttributeAccessIssue]  # simulate a lock held by another process
    note = await notes.add_recording(None, "Meeting", recording())
    library.list_notes = reload  # pyright: ignore[reportAttributeAccessIssue]  # restore the real query
    assert library.get_note(note.note_id).recordings[0].recording_id == "recording"


@run_async
async def test_display_options_are_saved_in_the_order_they_were_chosen(
    notes: NoteRepository,
) -> None:
    note, _events = await note_with_events(notes)
    library = notes._library
    save = library.set_display_options
    first_started = threading.Event()
    release_first = threading.Event()
    saved: list[DisplayOptions] = []

    def slow_save(note_id: str, options: DisplayOptions) -> None:
        if not saved:
            first_started.set()
            release_first.wait(5)
        saved.append(options)
        save(note_id, options)

    library.set_display_options = slow_save  # pyright: ignore[reportAttributeAccessIssue]  # hold the first write open
    off, on = DisplayOptions(False, True), DisplayOptions(True, True)
    first = asyncio.create_task(notes.set_display_options(note.note_id, off))
    await asyncio.to_thread(first_started.wait, 5)
    second = asyncio.create_task(notes.set_display_options(note.note_id, on))
    await asyncio.sleep(0.05)
    assert saved == []
    release_first.set()
    await asyncio.gather(first, second)
    assert saved == [off, on]
    assert (await notes.get_note(note.note_id)).display == on
