"""Turn unfinished microphone captures back into notes after a crash or quit."""

import asyncio
from collections.abc import Callable
from datetime import timedelta

from kikoe.core.models import Note, Recording, RecordingSource, TranscriptionState
from kikoe.media import transcode
from kikoe.storage.captures import CaptureJournal, CaptureStore
from kikoe.storage.errors import NoteNotFoundError
from kikoe.storage.notes import NoteRepository


async def recover_recording(
    journal: CaptureJournal,
    store: CaptureStore,
    notes: NoteRepository,
) -> Note | None:
    """Recover without uploading; a commit followed by a crash cannot create duplicates."""
    existing = await notes.find_recording_note(journal.recording_id)
    if existing is not None:
        await asyncio.to_thread(store.discard, journal)
        return existing
    duration = await asyncio.to_thread(store.finish, journal)
    if duration <= timedelta():
        await asyncio.to_thread(store.discard, journal)
        return None
    flac = store.flac_path(journal)
    await transcode.pcm_to_flac(store.pcm_path(journal), flac, journal.sample_rate)
    original = await notes.store_audio(flac, journal.recording_id)
    recording = Recording(
        journal.recording_id,
        journal.created_at,
        duration,
        RecordingSource.RECORDED,
        journal.provider_name,
        journal.model_id,
        audio_path=original,
        segments=await asyncio.to_thread(store.transcript, journal),
        transcription_state=TranscriptionState.PENDING,
    )
    try:
        note = await notes.add_recording(journal.note_id, journal.title, recording)
    except NoteNotFoundError:
        note = await notes.add_recording(None, journal.title, recording)
    await asyncio.to_thread(store.discard, journal)
    return note


async def recover_all(
    journals: tuple[CaptureJournal, ...],
    store: CaptureStore,
    notes: NoteRepository,
    on_recovered: Callable[[Note], None],
) -> None:
    """Recover each journal in turn; ``on_recovered`` sees every note that gained audio."""
    for journal in journals:
        note = await recover_recording(journal, store, notes)
        if note is not None:
            on_recovered(note)
