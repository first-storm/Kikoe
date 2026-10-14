"""Async access to the note library, with change notifications for the UI."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from kikoe.core.listeners import Listeners
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
        self._listeners = Listeners[str | None]()

    @property
    def notes(self) -> tuple[NoteSummary, ...]:
        """Summaries of all notes, newest first."""
        return self._notes

    def connect_changed(self, listener: NoteListener) -> Callable[[], None]:
        """Call ``listener`` after every change; returns a function that stops the calls."""
        return self._listeners.connect(listener)

    async def load(self) -> None:
        await self._change(None, self._library.migrate)

    async def search(self, query: str) -> tuple[NoteSummary, ...]:
        """Run title and transcript queries away from the GTK thread."""
        return await asyncio.to_thread(self._library.search_notes, query)

    async def get_note(self, note_id: str) -> Note:
        return await asyncio.to_thread(self._library.get_note, note_id)

    async def find_recording_note(self, recording_id: str) -> Note | None:
        return await asyncio.to_thread(self._library.find_recording_note, recording_id)

    async def create_note(self, title: str) -> Note:
        return await self._change(_id_of, self._library.create_note, title, datetime.now(UTC))

    async def rename_note(self, note_id: str, title: str) -> None:
        await self._change(note_id, self._library.rename_note, note_id, title.strip())

    async def set_display_options(self, note_id: str, options: DisplayOptions) -> None:
        await self._change(note_id, self._library.set_display_options, note_id, options)

    async def rename_speaker(self, note_id: str, speaker_id: str, name: str) -> None:
        """Persist a shared person name and refresh all consumers."""
        await self._change(note_id, self._library.rename_speaker, note_id, speaker_id, name)

    async def match_speakers(
        self, note_id: str, recording_id: str, choices: dict[str, str | None]
    ) -> Note:
        """Persist only confirmed dialog choices."""
        call = self._library.match_speakers
        return await self._change(note_id, call, note_id, recording_id, choices)

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
        return await self._change(
            note_id,
            self._library.update_segment,
            note_id,
            recording_id,
            position,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
        )

    async def delete_note(self, note_id: str) -> None:
        await self._change(note_id, self._library.delete_note, note_id)

    async def delete_recording(self, note_id: str, recording_id: str) -> Note:
        return await self._change(note_id, self._library.delete_recording, note_id, recording_id)

    async def store_audio(self, source: Path, recording_id: str) -> Path:
        return await asyncio.to_thread(self._library.store_audio, source, recording_id)

    async def discard_audio(self, path: Path) -> None:
        await asyncio.to_thread(self._library.discard_audio, path)

    async def add_recording(self, note_id: str | None, title: str, recording: Recording) -> Note:
        """Append to ``note_id``, or create a note called ``title`` when it is None."""
        return await self._change(_id_of, self._library.add_recording, note_id, title, recording)

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
        return await self._change(
            note_id,
            self._library.update_transcription,
            note_id,
            recording_id,
            state,
            segments=segments,
            provider_name=provider_name,
            model_id=model_id,
        )

    async def _change[**P, R](
        self,
        changed: str | Callable[[R], str] | None,
        call: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Write away from the GTK thread; ``changed`` may find the note's ID in the result."""
        result = await asyncio.to_thread(call, *args, **kwargs)
        self._notes = await asyncio.to_thread(self._library.list_notes)
        self._listeners.emit(changed(result) if callable(changed) else changed)
        return result


def _id_of(note: Note) -> str:
    return note.note_id
