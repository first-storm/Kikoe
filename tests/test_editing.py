"""Edits stay ordered even when undo and note changes race with storage."""

import asyncio
from collections.abc import Coroutine
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.models import Note, Recording, RecordingSource, Segment
from kikoe.services.editing import EditingSession
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository


def start_task(coroutine: Coroutine[None, None, None]) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine)


async def repository(tmp_path: Path) -> tuple[NoteRepository, Note]:
    notes = NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))
    await notes.load()
    note = await notes.add_recording(
        None,
        "Meeting",
        Recording(
            "recording",
            datetime.now(UTC),
            timedelta(seconds=5),
            RecordingSource.IMPORTED,
            "Cloud",
            "Model",
            (Segment("Original"),),
        ),
    )
    return notes, note


@run_async
async def test_undo_cannot_overtake_pending_edit(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    saved: list[Note] = []
    session = EditingSession(notes, start_task, saved.append)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    assert session.pending
    assert session.undo() is not None
    await session.wait_saved()
    assert [n.recordings[0].segments[0].text for n in saved] == ["Edited", "Original"]
    assert (await notes.get_note(note.note_id)).recordings[0].segments[0].text == "Original"
    assert session.redo() is not None
    await session.wait_saved()
    assert saved[-1].recordings[0].segments[0].text == "Edited"


@run_async
async def test_note_switch_resets_history_but_waits_for_old_writes(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    other = await notes.create_note("Other")
    saved: list[Note] = []
    session = EditingSession(notes, start_task, saved.append)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    session.set_note(other)
    assert not session.history.can_undo
    assert session.pending
    await session.wait_saved()
    assert saved[-1].note_id == note.note_id
    assert await notes.get_note(other.note_id) == other


@run_async
async def test_invalid_or_unchanged_edit_does_not_save(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    session = EditingSession(notes, start_task, lambda _note: None)
    session.set_note(note)
    session.edit("missing", 0, lambda fields: replace(fields, text="Edited"))
    session.edit("recording", 10, lambda fields: replace(fields, text="Edited"))
    session.edit("recording", 0, lambda fields: fields)
    assert not session.pending
    assert not session.history.can_undo


@run_async
async def test_pending_fields_preserve_previous_edit_and_timestamp_precision(
    tmp_path: Path,
) -> None:
    notes, note = await repository(tmp_path)
    session = EditingSession(notes, start_task, lambda _note: None)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    session.edit("recording", 0, lambda fields: replace(fields, start=timedelta(milliseconds=1250)))
    await session.wait_saved()
    segment = (await notes.get_note(note.note_id)).recordings[0].segments[0]
    assert segment.text == "Edited"
    assert segment.start == timedelta(milliseconds=1250)


@run_async
async def test_failed_save_reaches_task_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes, note = await repository(tmp_path)

    async def fail(*_args: object, **_kwargs: object) -> Note:
        raise ValueError("storage failed")

    monkeypatch.setattr(notes, "update_segment", fail)
    tasks: list[asyncio.Task[None]] = []

    def start(coroutine: Coroutine[None, None, None]) -> asyncio.Task[None]:
        task = start_task(coroutine)
        tasks.append(task)
        return task

    session = EditingSession(
        notes, start, lambda _note: pytest.fail("Failed writes cannot publish")
    )
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    with pytest.raises(ValueError, match="storage failed"):
        await tasks[0]
