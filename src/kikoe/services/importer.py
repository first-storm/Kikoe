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
from kikoe.core.transcription import FileTranscriptSnapshot, scope_speakers
from kikoe.i18n import _
from kikoe.media import transcode
from kikoe.providers.base import (
    FileTranscriptCallback,
    TranscriptionError,
    TranscriptionOptions,
    TranscriptionProvider,
    UploadLimits,
)
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
    on_update: FileTranscriptCallback | None = None,
    work_dir: Path | None = None,
) -> Note:
    """Transcribe ``source`` into ``note_id``, or into a new note named after the file.

    The original is copied into the library first. If anything fails or the task is
    cancelled, the copy is removed again and nothing is saved.
    """
    recording_id = uuid.uuid4().hex
    if model.capabilities.file is None:
        raise TranscriptionError(_("Choose a file transcription model to import audio."))
    original = await notes.store_audio(source, recording_id)
    try:
        duration, segments = await transcribe_audio(
            original,
            model,
            client=client,
            options=options,
            on_progress=on_progress,
            on_update=on_update,
            work_dir=work_dir,
        )
        on_progress(ImportStage.SAVING, 1, 1)
        recording = Recording(
            recording_id=recording_id,
            created_at=datetime.now(UTC),
            duration=duration,
            source=RecordingSource.IMPORTED,
            provider_name=provider.name,
            model_id=model.model_id,
            segments=scope_speakers(segments, recording_id),
            audio_path=original,
        )
        commit = asyncio.create_task(notes.add_recording(note_id, source.stem, recording))
        try:
            note = await asyncio.shield(commit)
        except asyncio.CancelledError:
            note = await commit
    except BaseException:
        await notes.discard_audio(original)
        raise
    log.info("Imported %s into note %s", source.name, note.note_id)
    return note


async def transcribe_audio(
    source: Path,
    model: ModelSpec,
    *,
    client: TranscriptionProvider,
    options: TranscriptionOptions | None = None,
    on_progress: ProgressCallback = _ignore_progress,
    on_update: FileTranscriptCallback | None = None,
    work_dir: Path | None = None,
) -> tuple[timedelta, tuple[Segment, ...]]:
    """Share conversion and chunk offsets without deciding who owns the original audio."""
    directory = work_dir or await asyncio.to_thread(paths.cache_dir)
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    job = _Job(uuid.uuid4().hex, directory, on_progress)
    on_progress(ImportStage.CONVERTING, 1, 1)
    try:
        limits = client.upload_limits(model, options or TranscriptionOptions())
        duration, parts = await job.prepare(source, limits)
        segments: list[Segment] = []
        for index, (path, offset) in enumerate(parts, start=1):
            on_progress(ImportStage.TRANSCRIBING, index, len(parts))

            def update(
                snapshot: FileTranscriptSnapshot,
                *,
                scope: str = str(index),
                part_offset: timedelta = offset,
            ) -> None:
                if on_update is not None:
                    on_update(
                        FileTranscriptSnapshot(
                            (
                                *segments,
                                *offset_segments(
                                    scope_speakers(snapshot.segments, scope), part_offset
                                ),
                            ),
                            offset_segments(scope_speakers(snapshot.preview, scope), part_offset),
                        )
                    )

            transcript = await client.transcribe_file(
                path,
                model,
                options or TranscriptionOptions(),
                on_update=update,
            )
            segments.extend(offset_segments(scope_speakers(transcript, str(index)), offset))
            if on_update is not None:
                on_update(FileTranscriptSnapshot(tuple(segments)))
        return duration, tuple(segments)
    finally:
        await job.clean_up()


class _Job:
    """Temporary upload files of one import, removed when it ends."""

    def __init__(self, recording_id: str, work_dir: Path, on_progress: ProgressCallback) -> None:
        self.recording_id = recording_id
        self._work_dir = work_dir
        self._on_progress = on_progress
        self._files: list[Path] = []

    async def prepare(
        self, original: Path, limits: UploadLimits
    ) -> tuple[timedelta, list[tuple[Path, timedelta]]]:
        """Convert for upload; return the length and the files to send with their offsets."""
        upload = self._file("")
        duration = await transcode.to_upload(original, upload)
        size = (await asyncio.to_thread(upload.stat)).st_size
        limit = limits.bytes
        max_duration = limits.duration
        if (limit is None or size <= limit) and (max_duration is None or duration <= max_duration):
            return duration, [(upload, timedelta())]

        silences = await transcode.find_silences(original)
        budget = duration
        if limit is not None and size > limit:
            budget = min(budget, duration * (limit / size) * SPLIT_MARGIN)
        if max_duration is not None:
            budget = min(budget, max_duration * SPLIT_MARGIN)
        chunks = plan_chunks(duration, silences, budget)
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
