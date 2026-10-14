"""Transcribe finished audio with the model the user picked.

Import, retrying a failed recording and re-transcribing all need the same preparation
(the provider's API key and a client for it) and differ in what a failure leaves behind.
"""

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from kikoe.core.choice import ModelChoice
from kikoe.core.models import Note, Provider, Recording, TranscriptionState
from kikoe.core.transcription import scope_speakers
from kikoe.i18n import _
from kikoe.providers.base import (
    FileTranscriptCallback,
    TranscriptionError,
    TranscriptionOptions,
    TranscriptionProvider,
)
from kikoe.providers.factory import create_client
from kikoe.services.commits import finish_commit
from kikoe.services.importer import ImportStage, ProgressCallback, import_audio, transcribe_audio
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
        return await self._transcribe_saved(
            note_id, recording, choice, options, on_progress, on_update
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
        """Transcribe a recording that has no text yet; any failure marks it as failed."""
        mark = functools.partial(self._notes.update_transcription, note_id, recording.recording_id)
        await mark(TranscriptionState.PENDING)
        try:
            return await self._transcribe_saved(
                note_id, recording, choice, options, on_progress, on_update
            )
        except BaseException:
            # Includes cancellation and key or client errors, so it never stays pending.
            await mark(TranscriptionState.FAILED)
            raise

    async def _client(self, provider: Provider) -> TranscriptionProvider:
        return self._create(provider, await self._keys.get_api_key(provider.provider_id))

    async def _transcribe_saved(
        self,
        note_id: str,
        recording: Recording,
        choice: ModelChoice,
        options: TranscriptionOptions,
        on_progress: ProgressCallback,
        on_update: FileTranscriptCallback,
    ) -> Note:
        """Update a saved recording from its retained audio, which is never removed."""
        if recording.audio_path is None:
            raise ValueError("A recording needs retained audio before transcription")
        if choice.model.capabilities.file is None:
            raise TranscriptionError(
                _("Choose a file transcription model to retry this recording.")
            )
        _duration, segments = await transcribe_audio(
            recording.audio_path,
            choice.model,
            client=await self._client(choice.provider),
            provider=choice.provider,
            options=options,
            on_progress=on_progress,
            on_update=on_update,
        )
        on_progress(ImportStage.SAVING, 1, 1)
        return await finish_commit(
            self._notes.update_transcription(
                note_id,
                recording.recording_id,
                TranscriptionState.READY,
                segments=scope_speakers(segments, recording.recording_id),
                provider_name=choice.provider.name,
                model_id=choice.model.model_id,
            )
        )
