"""Async access to the note library, with change notifications for the UI."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from kikoe.core.models import DisplayOptions, Note, NoteSummary, Recording
from kikoe.storage.library import Library

type NoteListener = Callable[[str | None], None]
"""Called with the ID of the note that changed, or None after the initial load."""


class NoteRepository:
    """Keeps the sidebar summaries in memory and refreshes them after every change."""

    def __init__(self, library: Library) -> None:
        self._library = library
        self._notes: tuple[NoteSummary, ...] = ()
        self._listeners: list[NoteListener] = []

    @property
    def notes(self) -> tuple[NoteSummary, ...]:
        """Summaries of all notes, newest first."""
        return self._notes

    def connect_changed(self, listener: NoteListener) -> Callable[[], None]:
        """Call ``listener`` after every change; returns a function that stops the calls."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def load(self) -> None:
        await asyncio.to_thread(self._library.migrate)
        await self._refresh(None)

    async def get_note(self, note_id: str) -> Note:
        return await asyncio.to_thread(self._library.get_note, note_id)

    async def create_note(self, title: str) -> Note:
        note = await asyncio.to_thread(self._library.create_note, title, datetime.now(UTC))
        await self._refresh(note.note_id)
        return note

    async def rename_note(self, note_id: str, title: str) -> None:
        await asyncio.to_thread(self._library.rename_note, note_id, title.strip())
        await self._refresh(note_id)

    async def set_display_options(self, note_id: str, options: DisplayOptions) -> None:
        await asyncio.to_thread(self._library.set_display_options, note_id, options)
        await self._refresh(note_id)

    async def delete_note(self, note_id: str) -> None:
        await asyncio.to_thread(self._library.delete_note, note_id)
        await self._refresh(note_id)

    async def store_audio(self, source: Path, recording_id: str) -> Path:
        return await asyncio.to_thread(self._library.store_audio, source, recording_id)

    async def discard_audio(self, path: Path) -> None:
        await asyncio.to_thread(self._library.discard_audio, path)

    async def add_recording(self, note_id: str | None, title: str, recording: Recording) -> Note:
        """Append to ``note_id``, or create a note called ``title`` when it is None."""
        note = await asyncio.to_thread(self._library.add_recording, note_id, title, recording)
        await self._refresh(note.note_id)
        return note

    async def _refresh(self, changed: str | None) -> None:
        self._notes = await asyncio.to_thread(self._library.list_notes)
        for listener in self._listeners:
            listener(changed)
