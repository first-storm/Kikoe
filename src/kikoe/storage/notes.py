"""Async access to the note library, with change notifications for the UI."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
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
from kikoe.storage.library import Library, LibraryError

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SummariesChanged:
    """The sidebar summaries were reloaded; ``note_id`` is None after the initial load."""

    note_id: str | None


@dataclass(frozen=True, slots=True)
class ContentChanged:
    """A note's transcript, speakers or display changed, but not its summary."""

    note_id: str


type NoteEvent = SummariesChanged | ContentChanged
type NoteListener = Callable[[NoteEvent], None]


class NoteRepository:
    """Keeps the sidebar summaries in memory and tells screens what changed.

    Summaries are reloaded only after changes that can alter them (title, recordings,
    deletion). Segment edits notify nobody: the editor already shows them, and the
    summaries cannot change.
    """

    def __init__(self, library: Library) -> None:
        self._library = library
        self._notes: tuple[NoteSummary, ...] = ()
        self._listeners = Listeners[NoteEvent]()

    @property
    def notes(self) -> tuple[NoteSummary, ...]:
        """Summaries of all notes, newest first."""
        return self._notes

    def connect_changed(self, listener: NoteListener) -> Callable[[], None]:
        """Call ``listener`` after every change; returns a function that stops the calls."""
        return self._listeners.connect(listener)

    async def load(self) -> None:
        await asyncio.to_thread(self._library.migrate)
        self._notes = await asyncio.to_thread(self._library.list_notes)
        self._listeners.emit(SummariesChanged(None))

    async def search(self, query: str) -> tuple[NoteSummary, ...]:
        """Run title and transcript queries away from the GTK thread."""
        return await asyncio.to_thread(self._library.search_notes, query)

    async def get_note(self, note_id: str) -> Note:
        return await asyncio.to_thread(self._library.get_note, note_id)

    async def find_recording_note(self, recording_id: str) -> Note | None:
        return await asyncio.to_thread(self._library.find_recording_note, recording_id)

    async def create_note(self, title: str) -> Note:
        call = self._library.create_note
        return await self._change_summaries(_id_of, call, title, datetime.now(UTC))

    async def rename_note(self, note_id: str, title: str) -> None:
        await self._change_summaries(note_id, self._library.rename_note, note_id, title.strip())

    async def set_display_options(self, note_id: str, options: DisplayOptions) -> None:
        await self._change_content(note_id, self._library.set_display_options, note_id, options)

    async def rename_speaker(self, note_id: str, speaker_id: str, name: str) -> None:
        """Persist a shared person name and refresh all consumers."""
        call = self._library.rename_speaker
        await self._change_content(note_id, call, note_id, speaker_id, name)

    async def match_speakers(
        self, note_id: str, recording_id: str, choices: dict[str, str | None]
    ) -> Note:
        """Persist only confirmed dialog choices."""
        call = self._library.match_speakers
        return await self._change_content(note_id, call, note_id, recording_id, choices)

    async def update_segment(
        self,
        note_id: str,
        recording_id: str,
        position: int,
        *,
        text: str,
        start_ms: int | None,
        end_ms: int | None,
    ) -> None:
        """Persist an edited segment's text and timestamps; the editor shows it itself."""
        await asyncio.to_thread(
            self._library.update_segment,
            note_id,
            recording_id,
            position,
            text=text,
            start_ms=start_ms,
            end_ms=end_ms,
        )

    async def delete_note(self, note_id: str) -> None:
        await self._change_summaries(note_id, self._library.delete_note, note_id)

    async def delete_recording(self, note_id: str, recording_id: str) -> Note:
        call = self._library.delete_recording
        return await self._change_summaries(note_id, call, note_id, recording_id)

    async def store_audio(self, source: Path, recording_id: str) -> Path:
        """Copy ``source`` into the library; a cancelled copy is removed, not left behind."""
        copying = asyncio.ensure_future(
            asyncio.to_thread(self._library.audio.store, source, recording_id)
        )
        try:
            return await asyncio.shield(copying)
        except asyncio.CancelledError:
            # The thread copies on regardless, so wait for it before removing the file.
            await asyncio.wait([copying])
            if not copying.cancelled() and copying.exception() is None:
                await self.discard_audio(copying.result())
            raise

    async def discard_audio(self, path: Path) -> None:
        await asyncio.to_thread(self._library.audio.discard, path)

    async def add_recording(self, note_id: str | None, title: str, recording: Recording) -> Note:
        """Append to ``note_id``, or create a note called ``title`` when it is None."""
        call = self._library.add_recording
        return await self._change_summaries(_id_of, call, note_id, title, recording)

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
        return await self._change_content(
            note_id,
            self._library.update_transcription,
            note_id,
            recording_id,
            state,
            segments=segments,
            provider_name=provider_name,
            model_id=model_id,
        )

    async def _change_summaries[**P, R](
        self,
        changed: str | Callable[[R], str] | None,
        call: Callable[P, R],
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> R:
        """Write, then reload the summaries; ``changed`` may find the note's ID in the result.

        Once the write is committed a failed reload must not look like a failed write:
        callers would undo work (such as deleting the audio) that the library now relies on.
        """
        result = await asyncio.to_thread(call, *args, **kwargs)
        try:
            self._notes = await asyncio.to_thread(self._library.list_notes)
        except LibraryError:
            log.warning("Could not reload the note summaries after a saved change", exc_info=True)
        self._listeners.emit(SummariesChanged(changed(result) if callable(changed) else changed))
        return result

    async def _change_content[**P, R](
        self, note_id: str, call: Callable[P, R], *args: P.args, **kwargs: P.kwargs
    ) -> R:
        """Write a change that leaves every summary as it was."""
        result = await asyncio.to_thread(call, *args, **kwargs)
        self._listeners.emit(ContentChanged(note_id))
        return result


def _id_of(note: Note) -> str:
    return note.note_id
