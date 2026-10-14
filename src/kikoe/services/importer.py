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
from kikoe.core.models import (
    ModelSpec,
    Note,
    Provider,
    Recording,
    RecordingSource,
    Segment,
    UploadSettings,
)
from kikoe.core.transcription import FileTranscriptSnapshot, scope_speakers
from kikoe.core.upload import UPLOAD_FORMATS, UploadFormat, upload_policy, validate_upload
from kikoe.i18n import _
from kikoe.media import transcode
from kikoe.providers.base import (
    FileTranscriptCallback,
    TranscriptionError,
    TranscriptionOptions,
    TranscriptionProvider,
    UploadLimits,
)
from kikoe.services.upload_queue import request_slot
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
            provider=provider,
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
    provider: Provider | None = None,
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
        settings = provider.upload if provider else UploadSettings(parallelism=1)
        if provider is not None:
            validate_upload(settings, provider.api)
            policy = upload_policy(provider.api)
            size = min(settings.chunk_bytes or policy.bytes, limits.bytes or policy.bytes)
            seconds = settings.chunk_seconds or policy.seconds
            duration_limit = timedelta(seconds=seconds) if seconds else limits.duration
            if limits.duration is not None:
                duration_limit = min(duration_limit or limits.duration, limits.duration)
            limits = UploadLimits(
                size, duration_limit, UPLOAD_FORMATS[settings.format or policy.format]
            )
        duration, parts = await job.prepare(source, limits)
        segments = await _transcribe_parts(
            parts,
            model,
            client,
            options or TranscriptionOptions(),
            settings.parallelism,
            on_progress,
            on_update,
            provider.provider_id if provider else job.recording_id,
        )
        return duration, segments
    finally:
        await job.clean_up()


async def _transcribe_parts(
    parts: list[tuple[Path, timedelta]],
    model: ModelSpec,
    client: TranscriptionProvider,
    options: TranscriptionOptions,
    parallelism: int,
    on_progress: ProgressCallback,
    on_update: FileTranscriptCallback | None,
    provider_id: str,
) -> tuple[Segment, ...]:
    """Bound workers and drain cancellations before the upload files are deleted."""
    pending = iter(enumerate(parts))
    results: list[tuple[Segment, ...] | None] = [None] * len(parts)
    snapshots = [FileTranscriptSnapshot(()) for _ in parts]
    completed = 0
    on_progress(ImportStage.TRANSCRIBING, 0, len(parts))

    def publish() -> None:
        if on_update is None:
            return
        segments: list[Segment] = []
        preview: tuple[Segment, ...] = ()
        for index, result in enumerate(results):
            if result is None:
                segments.extend(snapshots[index].segments)
                preview = snapshots[index].preview
                break
            segments.extend(result)
        on_update(FileTranscriptSnapshot(tuple(segments), preview))

    async def transcribe(index: int, path: Path, offset: timedelta) -> None:
        nonlocal completed
        scope = str(index + 1)

        def update(snapshot: FileTranscriptSnapshot) -> None:
            snapshots[index] = FileTranscriptSnapshot(
                offset_segments(scope_speakers(snapshot.segments, scope), offset),
                offset_segments(scope_speakers(snapshot.preview, scope), offset),
            )
            publish()

        transcript = await client.transcribe_file(path, model, options, on_update=update)
        results[index] = offset_segments(scope_speakers(transcript, scope), offset)
        completed += 1
        on_progress(ImportStage.TRANSCRIBING, completed, len(parts))
        publish()

    async def worker() -> None:
        for index, (path, offset) in pending:
            async with request_slot(provider_id, parallelism):
                await transcribe(index, path, offset)

    tasks = [asyncio.create_task(worker()) for _ in range(min(parallelism, len(parts)))]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            # gather already cancels workers when its owner is cancelled. A second
            # cancellation would interrupt the SDK's asynchronous resource cleanup.
            if not task.done() and not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return tuple(segment for result in results if result is not None for segment in result)


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
        upload = self._file("", limits.format)
        duration = await transcode.to_upload(original, upload, format=limits.format)
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
        parts: list[tuple[Path, timedelta]] = []
        for index, chunk in enumerate(chunks, start=1):
            self._on_progress(ImportStage.CONVERTING, index, len(chunks))
            parts.extend(await self._convert_part(original, chunk, limits))
        return duration, parts

    async def _convert_part(
        self,
        original: Path,
        chunk: TimeSpan,
        limits: UploadLimits,
    ) -> list[tuple[Path, timedelta]]:
        """Recheck each encoded chunk: variable bitrate can defeat the initial estimate."""
        path = self._file(f"-{len(self._files)}", limits.format)
        duration = await transcode.to_upload(original, path, chunk.start, chunk.end, limits.format)
        size = (await asyncio.to_thread(path.stat)).st_size
        if (limits.bytes is None or size <= limits.bytes) and (
            limits.duration is None or duration <= limits.duration
        ):
            return [(path, chunk.start)]
        await asyncio.to_thread(path.unlink, missing_ok=True)
        if chunk.end - chunk.start <= timedelta(milliseconds=10):
            raise TranscriptionError(_("The chunk size is too small for this audio format."))
        first = await self._convert_part(original, TimeSpan(chunk.start, chunk.middle), limits)
        second = await self._convert_part(original, TimeSpan(chunk.middle, chunk.end), limits)
        return first + second

    async def clean_up(self) -> None:
        for path in self._files:
            await asyncio.to_thread(path.unlink, missing_ok=True)

    def _file(self, suffix: str, format: UploadFormat) -> Path:
        path = self._work_dir / f"{self.recording_id}{suffix}{format.suffix}"
        self._files.append(path)
        return path
