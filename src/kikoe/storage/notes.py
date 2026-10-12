"""Async access to the note library, with change notifications for the UI."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from kikoe.core.models import (
    DisplayOptions,
    Note,
    NoteSummary,
    Recording,
    Segment,
    TranscriptionState,
)
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

    async def search(self, query: str) -> tuple[NoteSummary, ...]:
        """Run title and transcript queries away from the GTK thread."""
        return await asyncio.to_thread(self._library.search_notes, query)

    async def get_note(self, note_id: str) -> Note:
        return await asyncio.to_thread(self._library.get_note, note_id)

    async def find_recording_note(self, recording_id: str) -> Note | None:
        return await asyncio.to_thread(self._library.find_recording_note, recording_id)

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

    async def rename_speaker(self, note_id: str, speaker_id: str, name: str) -> None:
        """Persist a shared person name and refresh all consumers."""
        await asyncio.to_thread(self._library.rename_speaker, note_id, speaker_id, name)
        await self._refresh(note_id)

    async def match_speakers(
        self, note_id: str, recording_id: str, choices: dict[str, str | None]
    ) -> Note:
        """Persist only confirmed dialog choices."""
        note = await asyncio.to_thread(self._library.match_speakers, note_id, recording_id, choices)
        await self._refresh(note_id)
        return note

    async def update_segment(
        self,
        note_id: str,
        recording_id: str,
        position: int,
        *,
        text: str,
        start_ms: int | None,
        end_ms: int | None,
    ) -> Note:
        """Persist an edited segment's text and timestamps."""
        note = await asyncio.to_thread(
            self._library.update_segment,
            note_id,
            recording_id,
            position,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        await self._refresh(note_id)
        return note

    async def delete_note(self, note_id: str) -> None:
        await asyncio.to_thread(self._library.delete_note, note_id)
        await self._refresh(note_id)

    async def delete_recording(self, note_id: str, recording_id: str) -> Note:
        """Delete one recording and refresh all consumers."""
        note = await asyncio.to_thread(self._library.delete_recording, note_id, recording_id)
        await self._refresh(note_id)
        return note

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

    async def update_transcription(
        self,
        note_id: str,
        recording_id: str,
        state: TranscriptionState,
        *,
        segments: tuple[Segment, ...] | None = None,
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> Note:
        """Persist the result without adding another recording on retries."""
        note = await asyncio.to_thread(
            self._library.update_transcription,
            note_id,
            recording_id,
            state,
            segments=segments,
            provider_name=provider_name,
            model_id=model_id,
        )
        await self._refresh(note_id)
        return note
