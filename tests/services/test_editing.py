"""Edits show at once and stay ordered even when undo and note reloads race with storage."""

import asyncio
from collections.abc import Coroutine
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kikoe.core.models import Note, Recording, RecordingSource, Segment, TranscriptionState
from kikoe.services.editing import EditingSession
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository
from tests.helpers import run_async


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
            (Segment("Original"), Segment("Second")),
        ),
    )
    return notes, note


def text(note: Note, position: int = 0) -> str:
    return note.recordings[0].segments[position].text


async def saved_text(notes: NoteRepository, note: Note, position: int = 0) -> str:
    return text(await notes.get_note(note.note_id), position)


@run_async
async def test_edit_is_shown_before_it_is_saved(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    applied: list[tuple[str, str, int]] = []
    session = EditingSession(
        notes, start_task, lambda shown, rid, pos: applied.append((text(shown, pos), rid, pos))
    )
    session.set_note(note)
    session.edit("recording", 1, lambda fields: replace(fields, text="Edited"))
    assert applied == [("Edited", "recording", 1)]
    assert session.pending
    await session.wait_saved()
    assert await saved_text(notes, note, 1) == "Edited"


@run_async
async def test_undo_cannot_overtake_pending_edit(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    shown: list[Note] = []
    session = EditingSession(notes, start_task, lambda n, _rid, _pos: shown.append(n))
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    assert session.undo() is not None
    await session.wait_saved()
    assert [text(n) for n in shown] == ["Edited", "Original"]
    assert await saved_text(notes, note) == "Original"
    assert session.redo() is not None
    await session.wait_saved()
    assert text(shown[-1]) == "Edited"
    assert await saved_text(notes, note) == "Edited"


@run_async
async def test_reloaded_note_keeps_edits_that_are_still_being_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes, note = await repository(tmp_path)
    write = notes.update_segment
    saving, release_save = asyncio.Event(), asyncio.Event()

    async def blocked_write(
        note_id: str,
        recording_id: str,
        position: int,
        *,
        text: str,
        start_ms: int | None,
        end_ms: int | None,
    ) -> None:
        saving.set()
        await release_save.wait()
        await write(note_id, recording_id, position, text=text, start_ms=start_ms, end_ms=end_ms)

    monkeypatch.setattr(notes, "update_segment", blocked_write)
    session = EditingSession(notes, start_task, lambda _n, _rid, _pos: None)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    try:
        async with asyncio.timeout(5):
            await saving.wait()
            stale = await notes.get_note(note.note_id)
            assert text(stale) == "Original"
            reloaded = session.set_note(stale)
            assert reloaded is not None and text(reloaded) == "Edited"
    finally:
        release_save.set()
        await session.wait_saved()
    fresh = session.set_note(await session.read_note(note.note_id))
    assert fresh is not None and text(fresh) == "Edited"


@run_async
async def test_saved_edits_no_longer_override_reloads(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    session = EditingSession(notes, start_task, lambda _n, _rid, _pos: None)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    await session.wait_saved()
    # Another writer, e.g. a re-transcription, may legitimately change the text later.
    other = replace(
        note, recordings=(replace(note.recordings[0], segments=(Segment("New"), Segment("B"))),)
    )
    reloaded = session.set_note(other)
    assert reloaded is not None and text(reloaded) == "New"


@run_async
async def test_note_switch_resets_history_but_waits_for_old_writes(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    other = await notes.create_note("Other")
    session = EditingSession(notes, start_task, lambda _n, _rid, _pos: None)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    assert session.set_note(other) == other
    assert not session.history.can_undo
    assert session.pending
    await session.wait_saved()
    assert await saved_text(notes, note) == "Edited"
    assert await notes.get_note(other.note_id) == other


@run_async
async def test_invalid_or_unchanged_edit_does_not_save(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    session = EditingSession(
        notes, start_task, lambda _n, _rid, _pos: pytest.fail("Nothing was edited")
    )
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
    session = EditingSession(notes, start_task, lambda _n, _rid, _pos: None)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    session.edit("recording", 0, lambda fields: replace(fields, start=timedelta(milliseconds=1250)))
    await session.wait_saved()
    segment = (await notes.get_note(note.note_id)).recordings[0].segments[0]
    assert segment.text == "Edited"
    assert segment.start == timedelta(milliseconds=1250)


@run_async
async def test_failed_save_reaches_task_owner_and_keeps_the_shown_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes, note = await repository(tmp_path)

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise ValueError("storage failed")

    monkeypatch.setattr(notes, "update_segment", fail)
    tasks: list[asyncio.Task[None]] = []

    def start(coroutine: Coroutine[None, None, None]) -> asyncio.Task[None]:
        task = start_task(coroutine)
        tasks.append(task)
        return task

    session = EditingSession(notes, start, lambda _n, _rid, _pos: None)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    with pytest.raises(ValueError, match="storage failed"):
        await tasks[0]
    reloaded = session.set_note(note)
    assert reloaded is not None and text(reloaded) == "Edited"


@run_async
async def test_a_reload_cannot_return_text_from_before_a_finished_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes, note = await repository(tmp_path)
    write, read = notes.update_segment, notes.get_note
    saving, release_save = asyncio.Event(), asyncio.Event()
    reload_requested, reading = asyncio.Event(), asyncio.Event()

    async def blocked_write(
        note_id: str,
        recording_id: str,
        position: int,
        *,
        text: str,
        start_ms: int | None,
        end_ms: int | None,
    ) -> None:
        saving.set()
        await release_save.wait()
        await write(note_id, recording_id, position, text=text, start_ms=start_ms, end_ms=end_ms)

    async def observed_read(note_id: str) -> Note:
        reading.set()
        return await read(note_id)

    monkeypatch.setattr(notes, "update_segment", blocked_write)
    monkeypatch.setattr(notes, "get_note", observed_read)
    session = EditingSession(notes, start_task, lambda _n, _rid, _pos: None)

    async def reload_note() -> Note:
        reload_requested.set()
        return await session.read_note(note.note_id)

    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    reload_task: asyncio.Task[Note] | None = None
    try:
        async with asyncio.timeout(5):
            await saving.wait()
            reload_task = asyncio.create_task(reload_note())
            await reload_requested.wait()
            assert not reading.is_set()
            assert not reload_task.done()
    finally:
        release_save.set()
        await session.wait_saved()
        if reload_task is not None:
            await reload_task
    assert reload_task is not None
    reloaded = session.set_note(reload_task.result())
    assert reloaded is not None and text(reloaded) == "Edited"


@run_async
async def test_undo_cannot_overwrite_a_retranscription_of_the_same_shape(tmp_path: Path) -> None:
    notes, note = await repository(tmp_path)
    session = EditingSession(notes, start_task, lambda _n, _rid, _pos: None)
    session.set_note(note)
    session.edit("recording", 0, lambda fields: replace(fields, text="Edited"))
    await session.wait_saved()
    await notes.update_transcription(
        note.note_id,
        "recording",
        TranscriptionState.READY,
        segments=(Segment("New first"), Segment("New second")),
        provider_name="Cloud",
        model_id="Model",
    )

    session.set_note(await session.read_note(note.note_id))

    assert not session.history.can_undo
    assert session.undo() is None
    assert await saved_text(notes, note) == "New first"
