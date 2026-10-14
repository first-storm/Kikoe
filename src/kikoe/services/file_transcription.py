"""Transcribe finished audio with the model the user picked.

Import, retrying a failed recording and re-transcribing all need the same preparation
(the provider's API key and a client for it) and differ in what a failure leaves behind.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from kikoe.core.choice import ModelChoice
from kikoe.core.models import Note, Provider, Recording, TranscriptionState
from kikoe.providers.base import FileTranscriptCallback, TranscriptionOptions, TranscriptionProvider
from kikoe.providers.factory import create_client
from kikoe.services.errors import USER_FACING_ERRORS
from kikoe.services.importer import ProgressCallback, import_audio
from kikoe.services.recording import transcribe_recording
from kikoe.storage.notes import NoteRepository

type ClientFactory = Callable[[Provider, str], TranscriptionProvider]


class ApiKeys(Protocol):
    async def get_api_key(self, provider_id: str) -> str: ...


class FileTranscriptionWorkflow:
    """Turns a user's choice of audio and model into a saved transcript."""

    def __init__(
        self, keys: ApiKeys, notes: NoteRepository, create: ClientFactory = create_client
    ) -> None:
        self._keys = keys
        self._notes = notes
        self._create = create

    async def _client(self, provider: Provider) -> TranscriptionProvider:
        return self._create(provider, await self._keys.get_api_key(provider.provider_id))

    async def import_file(
        self,
        path: Path,
        note_id: str | None,
        choice: ModelChoice,
        options: TranscriptionOptions,
        *,
        on_progress: ProgressCallback,
        on_update: FileTranscriptCallback,
    ) -> Note:
        """Copy ``path`` into the library and transcribe it; nothing is saved on failure."""
        return await import_audio(
            path,
            note_id,
            choice.provider,
            choice.model,
            client=await self._client(choice.provider),
            notes=self._notes,
            options=options,
            on_progress=on_progress,
            on_update=on_update,
        )

    async def retranscribe(
        self,
        note_id: str,
        recording: Recording,
        choice: ModelChoice,
        options: TranscriptionOptions,
        *,
        on_progress: ProgressCallback,
        on_update: FileTranscriptCallback,
    ) -> Note:
        """Replace a recording's text; a failure keeps the existing text and audio."""
        return await transcribe_recording(
            note_id,
            recording,
            choice,
            client=await self._client(choice.provider),
            notes=self._notes,
            options=options,
            on_progress=on_progress,
            on_update=on_update,
            preserve_on_failure=True,
        )

    async def transcribe_pending(
        self,
        note_id: str,
        recording: Recording,
        choice: ModelChoice,
        options: TranscriptionOptions,
        *,
        on_progress: ProgressCallback,
        on_update: FileTranscriptCallback,
    ) -> Note:
        """Transcribe a recording that has no text yet; a failure marks it as failed."""
        try:
            return await transcribe_recording(
                note_id,
                recording,
                choice,
                client=await self._client(choice.provider),
                notes=self._notes,
                options=options,
                on_progress=on_progress,
                on_update=on_update,
            )
        except USER_FACING_ERRORS:
            # Also covers key and client errors raised before the service could mark it.
            await self._notes.update_transcription(
                note_id, recording.recording_id, TranscriptionState.FAILED
            )
            raise
