"""Import an audio file: keep the original, transcribe it, and save it as a recording."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from kikoe import paths
from kikoe.core.chunking import TimeSpan, offset_segments, plan_chunks
from kikoe.core.models import ModelSpec, Note, Provider, Recording, RecordingSource, Segment
from kikoe.media import transcode
from kikoe.providers.base import TranscriptionOptions, TranscriptionProvider
from kikoe.storage.notes import NoteRepository

log = logging.getLogger(__name__)

SPLIT_MARGIN = 0.9
"""Aim parts below the upload limit, since the bitrate varies along the recording."""


class ImportStage(StrEnum):
    CONVERTING = "converting"
    TRANSCRIBING = "transcribing"
    SAVING = "saving"


type ProgressCallback = Callable[[ImportStage, int, int], None]
"""Called with ``(stage, part, parts)``; both counts are 1 unless the audio was split."""


def _ignore_progress(_stage: ImportStage, _part: int, _parts: int) -> None:
    pass


async def import_audio(
    source: Path,
    note_id: str | None,
    provider: Provider,
    model: ModelSpec,
    *,
    client: TranscriptionProvider,
    notes: NoteRepository,
    options: TranscriptionOptions | None = None,
    on_progress: ProgressCallback = _ignore_progress,
    work_dir: Path | None = None,
) -> Note:
    """Transcribe ``source`` into ``note_id``, or into a new note named after the file.

    The original is copied into the library first. If anything fails or the task is
    cancelled, the copy is removed again and nothing is saved.
    """
    job = _Job(uuid.uuid4().hex, work_dir or paths.cache_dir(), on_progress)
    on_progress(ImportStage.CONVERTING, 1, 1)
    original = await notes.store_audio(source, job.recording_id)
    try:
        duration, parts = await job.prepare(original, client.max_upload_bytes)
        segments: list[Segment] = []
        for index, (path, offset) in enumerate(parts, start=1):
            on_progress(ImportStage.TRANSCRIBING, index, len(parts))
            transcript = await client.transcribe_file(
                path, model, options or TranscriptionOptions()
            )
            segments += offset_segments(transcript, offset)
        on_progress(ImportStage.SAVING, 1, 1)
        recording = Recording(
            recording_id=job.recording_id,
            created_at=datetime.now(UTC),
            duration=duration,
            source=RecordingSource.IMPORTED,
            provider_name=provider.name,
            model_id=model.model_id,
            segments=tuple(segments),
            audio_path=original,
        )
        note = await notes.add_recording(note_id, source.stem, recording)
    except BaseException:
        await notes.discard_audio(original)
        raise
    finally:
        await job.clean_up()
    log.info("Imported %s into note %s", source.name, note.note_id)
    return note


class _Job:
    """Temporary upload files of one import, removed when it ends."""

    def __init__(self, recording_id: str, work_dir: Path, on_progress: ProgressCallback) -> None:
        self.recording_id = recording_id
        self._work_dir = work_dir
        self._on_progress = on_progress
        self._files: list[Path] = []

    async def prepare(
        self, original: Path, limit: int | None
    ) -> tuple[timedelta, list[tuple[Path, timedelta]]]:
        """Convert for upload; return the length and the files to send with their offsets."""
        upload = self._file("")
        duration = await transcode.to_upload(original, upload)
        size = (await asyncio.to_thread(upload.stat)).st_size
        if limit is None or size <= limit:
            return duration, [(upload, timedelta())]

        silences = await transcode.find_silences(original)
        chunks = plan_chunks(duration, silences, duration * (limit / size) * SPLIT_MARGIN)
        log.info("Splitting %d bytes of audio into %d parts", size, len(chunks))
        return duration, [
            (await self._convert_part(original, chunk, index, len(chunks)), chunk.start)
            for index, chunk in enumerate(chunks, start=1)
        ]

    async def _convert_part(self, original: Path, chunk: TimeSpan, index: int, count: int) -> Path:
        self._on_progress(ImportStage.CONVERTING, index, count)
        path = self._file(f"-{index}")
        await transcode.to_upload(original, path, chunk.start, chunk.end)
        return path

    async def clean_up(self) -> None:
        for path in self._files:
            await asyncio.to_thread(path.unlink, missing_ok=True)

    def _file(self, suffix: str) -> Path:
        path = self._work_dir / f"{self.recording_id}{suffix}{transcode.UPLOAD_SUFFIX}"
        self._files.append(path)
        return path
