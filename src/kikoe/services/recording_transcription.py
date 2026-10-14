"""Transcribe the retained audio of a saved recording."""

from pathlib import Path

from kikoe.core.choice import ModelChoice
from kikoe.core.models import Note, Recording, TranscriptionState
from kikoe.core.transcription import scope_speakers
from kikoe.i18n import _
from kikoe.providers.base import (
    FileTranscriptCallback,
    TranscriptionError,
    TranscriptionOptions,
    TranscriptionProvider,
)
from kikoe.services.commits import finish_commit
from kikoe.services.importer import ImportStage, ProgressCallback, transcribe_audio
from kikoe.storage.notes import NoteRepository


async def transcribe_recording(
    note_id: str,
    recording: Recording,
    choice: ModelChoice,
    *,
    client: TranscriptionProvider,
    notes: NoteRepository,
    options: TranscriptionOptions,
    on_progress: ProgressCallback,
    on_update: FileTranscriptCallback | None = None,
    work_dir: Path | None = None,
    preserve_on_failure: bool = False,
) -> Note:
    """Update the saved recording; cancellation never removes its original audio."""
    if recording.audio_path is None:
        raise ValueError("A recording needs retained audio before transcription")
    if choice.model.capabilities.file is None:
        raise TranscriptionError(_("Choose a file transcription model to retry this recording."))
    if not preserve_on_failure:
        await notes.update_transcription(
            note_id, recording.recording_id, TranscriptionState.PENDING
        )
    try:
        _duration, segments = await transcribe_audio(
            recording.audio_path,
            choice.model,
            client=client,
            provider=choice.provider,
            options=options,
            on_progress=on_progress,
            on_update=on_update,
            work_dir=work_dir,
        )
        on_progress(ImportStage.SAVING, 1, 1)
        return await finish_commit(
            notes.update_transcription(
                note_id,
                recording.recording_id,
                TranscriptionState.READY,
                segments=scope_speakers(segments, recording.recording_id),
                provider_name=choice.provider.name,
                model_id=choice.model.model_id,
            )
        )
    except BaseException:
        if not preserve_on_failure:
            await notes.update_transcription(
                note_id, recording.recording_id, TranscriptionState.FAILED
            )
        raise
