import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from helpers import LIVE_ONLY, file_caps, run_async
from kikoe.core.choice import ModelChoice
from kikoe.core.chunking import TimeSpan
from kikoe.core.models import (
    ApiKind,
    AudioFormat,
    ModelSpec,
    Note,
    Provider,
    Recording,
    RecordingSource,
    Segment,
    UploadSettings,
)
from kikoe.core.transcription import FileTranscriptSnapshot, RealtimeProfile
from kikoe.core.upload import OGG_OPUS, UploadFormat, UploadLimits
from kikoe.media import transcode
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
)
from kikoe.services.importer import ImportStage, import_audio, transcribe_audio
from kikoe.storage.notes import NoteRepository

MODEL = ModelSpec("whisper-large-v3", "Whisper", file_caps(timestamps=True))
PROVIDER = Provider("p1", "Groq", ApiKind.OPENAI_COMPATIBLE, "https://example.test/v1", (MODEL,))
CHOICE = ModelChoice(PROVIDER, MODEL)


def s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


def write(path: Path, data: bytes) -> None:
    path.write_bytes(data)


class Stages(list[tuple[ImportStage, int, int]]):
    """Progress callback that records every call."""

    def __call__(self, stage: ImportStage, part: int, parts: int) -> None:
        self.append((stage, part, parts))


class FakeClient:
    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        self.options.append(options)
        return UploadLimits(self.max_upload_bytes)

    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        return RealtimeProfile()

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        raise TranscriptionError("Not a realtime fake")

    """Returns one timed segment per upload, numbered in call order."""

    def __init__(self, max_upload_bytes: int | None = 1000, fail: bool = False) -> None:
        self.max_upload_bytes = max_upload_bytes
        self.uploads: list[Path] = []
        self.options: list[TranscriptionOptions] = []
        self._fail = fail

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        self.uploads.append(audio)
        self.options.append(options)
        if self._fail:
            raise TranscriptionError("Invalid API Key")
        return (Segment(f"part {len(self.uploads)}", s(1), s(2)),)


class FakeConverter:
    """Stands in for ffmpeg: writes ``size`` bytes per full conversion."""

    def __init__(self, duration: timedelta, size: int) -> None:
        self.duration = duration
        self.size = size
        self.parts: list[TimeSpan] = []

    async def to_upload(
        self,
        source: Path,
        destination: Path,
        start: timedelta | None = None,
        end: timedelta | None = None,
        format: UploadFormat = OGG_OPUS,
    ) -> timedelta:
        if start is None or end is None:
            write(destination, b"x" * self.size)
            return self.duration
        self.parts.append(TimeSpan(start, end))
        write(destination, b"x")
        return end - start

    async def find_silences(self, source: Path) -> tuple[TimeSpan, ...]:
        return (TimeSpan(s(50), s(52)), TimeSpan(s(95), s(97)))


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "Team Sync.m4a"
    path.write_bytes(b"original")
    return path


def use_converter(monkeypatch: pytest.MonkeyPatch, converter: FakeConverter) -> None:
    monkeypatch.setattr(transcode, "to_upload", converter.to_upload)
    monkeypatch.setattr(transcode, "find_silences", converter.find_silences)


@pytest.mark.parametrize("options", [None, TranscriptionOptions(False, False, language="zh")])
@run_async
async def test_upload_limits_and_transcription_receive_the_requested_options(
    options: TranscriptionOptions | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
) -> None:
    use_converter(monkeypatch, FakeConverter(s(42), size=500))
    client = FakeClient()

    duration, segments = await transcribe_audio(
        source, CHOICE, client=client, options=options, work_dir=tmp_path / "work"
    )

    assert client.options == [options or TranscriptionOptions()] * 2
    assert duration == s(42)
    assert segments == (Segment("part 1", s(1), s(2)),)


@run_async
async def test_import_creates_a_note_named_after_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: NoteRepository, source: Path
) -> None:
    use_converter(monkeypatch, FakeConverter(s(42), size=500))
    await notes.load()
    work = tmp_path / "work"
    work.mkdir()
    stages = Stages()

    note = await import_audio(
        source,
        None,
        CHOICE,
        client=FakeClient(),
        notes=notes,
        on_progress=stages,
        work_dir=work,
    )

    assert note.title == "Team Sync"
    [recording] = note.recordings
    assert recording.duration == s(42)
    assert recording.source is RecordingSource.IMPORTED
    assert (recording.provider_name, recording.model_id) == ("Groq", "whisper-large-v3")
    assert recording.segments == (Segment("part 1", s(1), s(2)),)
    assert recording.audio_path is not None
    assert recording.audio_path.read_bytes() == b"original"
    assert list(work.iterdir()) == []
    assert [stage for stage, _, _ in stages] == [
        ImportStage.CONVERTING,
        ImportStage.TRANSCRIBING,
        ImportStage.TRANSCRIBING,
        ImportStage.SAVING,
    ]


@run_async
async def test_import_into_an_open_note_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: NoteRepository, source: Path
) -> None:
    use_converter(monkeypatch, FakeConverter(s(10), size=10))
    await notes.load()
    existing = await notes.create_note("Weekly")

    note = await import_audio(
        source,
        existing.note_id,
        CHOICE,
        client=FakeClient(),
        notes=notes,
        work_dir=tmp_path,
    )
    note = await import_audio(
        source,
        existing.note_id,
        CHOICE,
        client=FakeClient(),
        notes=notes,
        work_dir=tmp_path,
    )

    assert note.note_id == existing.note_id
    assert note.title == "Weekly"
    assert len(note.recordings) == 2


@run_async
async def test_oversize_audio_is_split_at_silences_and_times_shifted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: NoteRepository, source: Path
) -> None:
    # 120 s in 1500 bytes with a 1000-byte limit: parts of at most 120*1000/1500*0.9 = 72 s.
    converter = FakeConverter(s(120), size=1500)
    use_converter(monkeypatch, converter)
    await notes.load()
    client = FakeClient(max_upload_bytes=1000)
    stages = Stages()

    note = await import_audio(
        source,
        None,
        CHOICE,
        client=client,
        notes=notes,
        on_progress=stages,
        work_dir=tmp_path,
    )

    assert converter.parts == [TimeSpan(s(0), s(51)), TimeSpan(s(51), s(120))]
    assert note.recordings[0].duration == s(120)
    assert note.recordings[0].segments == (
        Segment("part 1", s(1), s(2)),
        Segment("part 2", s(52), s(53)),
    )
    assert (ImportStage.TRANSCRIBING, 2, 2) in stages
    assert len(client.uploads) == 2
    assert not any(path.exists() for path in client.uploads)


@run_async
async def test_failed_transcription_saves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: NoteRepository, source: Path
) -> None:
    use_converter(monkeypatch, FakeConverter(s(10), size=10))
    await notes.load()
    work = tmp_path / "work"
    work.mkdir()

    with pytest.raises(TranscriptionError, match="Invalid API Key"):
        await import_audio(
            source,
            None,
            CHOICE,
            client=FakeClient(fail=True),
            notes=notes,
            work_dir=work,
        )

    assert notes.notes == ()
    assert list((tmp_path / "audio").iterdir()) == []
    assert list(work.iterdir()) == []


class HangingClient(FakeClient):
    """Never answers; ``started`` is set once the upload begins."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        self.uploads.append(audio)
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@run_async
async def test_cancelling_removes_the_copied_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: NoteRepository, source: Path
) -> None:
    use_converter(monkeypatch, FakeConverter(s(10), size=10))
    await notes.load()
    client = HangingClient()
    task = asyncio.create_task(
        import_audio(source, None, CHOICE, client=client, notes=notes, work_dir=tmp_path)
    )
    await client.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert list((tmp_path / "audio").iterdir()) == []
    assert not client.uploads[0].exists()


@run_async
async def test_duration_limit_splits_even_small_compressed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: NoteRepository, source: Path
) -> None:
    class DurationClient(FakeClient):
        def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
            return UploadLimits(duration=s(60))

    converter = FakeConverter(s(120), size=100)
    use_converter(monkeypatch, converter)
    await notes.load()
    client = DurationClient()
    await import_audio(source, None, CHOICE, client=client, notes=notes, work_dir=tmp_path)
    assert len(client.uploads) >= 2
    assert all(part.end - part.start <= s(60) for part in converter.parts)


@run_async
async def test_registry_format_of_the_api_is_used_for_every_part(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path
) -> None:
    use_converter(monkeypatch, FakeConverter(s(120), size=1500))
    client = FakeClient(max_upload_bytes=1000)
    openai = ModelChoice(replace(PROVIDER, api=ApiKind.OPENAI), MODEL)
    await transcribe_audio(source, openai, client=client, work_dir=tmp_path)
    assert len(client.uploads) >= 2
    assert {path.suffix for path in client.uploads} == {".wav"}


@run_async
async def test_live_only_model_is_refused_before_copying_audio(
    tmp_path: Path, notes: NoteRepository, source: Path
) -> None:
    from dataclasses import replace

    provider = replace(PROVIDER, api=ApiKind.GEMINI)
    model = ModelSpec("gemini-3.5-transcribe-live", "Live", LIVE_ONLY)
    await notes.load()
    with pytest.raises(TranscriptionError, match="file transcription model"):
        await import_audio(
            source,
            None,
            ModelChoice(provider, model),
            client=FakeClient(),
            notes=notes,
            work_dir=tmp_path,
        )
    assert list((tmp_path / "audio").glob("*")) == []


class StreamingClient(FakeClient):
    """Send partial text before the authoritative result of each upload."""

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        self.uploads.append(audio)
        number = len(self.uploads)
        if on_update is not None:
            on_update(FileTranscriptSnapshot(preview=(Segment(f"preview {number}"),)))
            on_update(FileTranscriptSnapshot((Segment(f"draft {number}", s(1), s(2), "A"),)))
        if self._fail and number == 2:
            raise TranscriptionError("Second stream failed")
        return (Segment(f"final {number}", s(1), s(2), "A"),)


@run_async
async def test_file_stream_offsets_previews_and_keeps_previous_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
) -> None:
    use_converter(monkeypatch, FakeConverter(s(120), size=1500))
    updates: list[FileTranscriptSnapshot] = []
    client = StreamingClient()
    duration, result = await transcribe_audio(
        source,
        CHOICE,
        client=client,
        on_update=updates.append,
        work_dir=tmp_path / "work",
    )
    first = Segment("final 1", s(1), s(2), "1:A")
    second = Segment("final 2", s(52), s(53), "2:A")
    assert duration == s(120)
    assert result == (first, second)
    assert updates[2] == FileTranscriptSnapshot((first,))
    assert updates[3] == FileTranscriptSnapshot((first,), (Segment("preview 2"),))
    assert updates[4].segments == (first, Segment("draft 2", s(52), s(53), "2:A"))
    assert updates[-1] == FileTranscriptSnapshot(result)
    assert not any(path.exists() for path in client.uploads)


@run_async
async def test_failed_later_stream_does_not_save_earlier_completed_parts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notes: NoteRepository,
    source: Path,
) -> None:
    use_converter(monkeypatch, FakeConverter(s(120), size=1500))
    await notes.load()
    updates: list[FileTranscriptSnapshot] = []
    client = StreamingClient(fail=True)
    with pytest.raises(TranscriptionError, match="Second stream failed"):
        await import_audio(
            source,
            None,
            CHOICE,
            client=client,
            notes=notes,
            on_update=updates.append,
            work_dir=tmp_path / "work",
        )
    assert updates[-1].segments[0].text == "final 1"
    assert notes.notes == ()
    assert await asyncio.to_thread(source.read_bytes) == b"original"
    assert not any(path.exists() for path in client.uploads)


@run_async
async def test_cancel_during_import_commit_finishes_saving_audio_and_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    notes: NoteRepository,
    source: Path,
) -> None:
    use_converter(monkeypatch, FakeConverter(s(10), size=10))
    await notes.load()
    started, release = asyncio.Event(), asyncio.Event()
    original = notes.add_recording

    async def commit(note_id: str | None, title: str, recording: Recording) -> Note:
        started.set()
        await release.wait()
        return await original(note_id, title, recording)

    monkeypatch.setattr(notes, "add_recording", commit)
    task = asyncio.create_task(
        import_audio(
            source,
            None,
            CHOICE,
            client=FakeClient(),
            notes=notes,
            work_dir=tmp_path / "work",
        )
    )
    await started.wait()
    task.cancel()
    release.set()
    note = await task
    assert note.recordings[0].segments[0].text == "part 1"
    audio = note.recordings[0].audio_path
    assert audio is not None and audio.read_bytes() == b"original"


class ControlledClient(FakeClient):
    """Release requests individually to verify true concurrency and out-of-order results."""

    def __init__(self) -> None:
        super().__init__(max_upload_bytes=None)
        self.active = 0
        self.maximum = 0
        self.started: asyncio.Queue[int] = asyncio.Queue()
        self.release: dict[int, asyncio.Event] = {}
        self.cancelled: list[int] = []

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        number = len(self.uploads)
        self.uploads.append(audio)
        self.release[number] = asyncio.Event()
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        self.started.put_nowait(number)
        try:
            await self.release[number].wait()
            if on_update is not None:
                on_update(FileTranscriptSnapshot(preview=(Segment(str(number)),)))
            return (Segment(str(number), s(1), s(2), "A"),)
        except asyncio.CancelledError:
            assert await asyncio.to_thread(audio.exists), "cleanup must wait for every request"
            self.cancelled.append(number)
            raise
        finally:
            self.active -= 1


@run_async
async def test_parallel_chunks_finish_out_of_order_but_transcript_and_previews_stay_ordered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
) -> None:
    use_converter(monkeypatch, FakeConverter(s(12), size=100))
    provider = replace(PROVIDER, upload=UploadSettings(parallelism=2, chunk_seconds=5))
    client = ControlledClient()
    stages = Stages()
    updates: list[FileTranscriptSnapshot] = []
    async with asyncio.timeout(5):
        task = asyncio.create_task(
            transcribe_audio(
                source,
                ModelChoice(provider, MODEL),
                client=client,
                work_dir=tmp_path / "work",
                on_progress=stages,
                on_update=updates.append,
            )
        )
        assert await client.started.get() == 0
        assert await client.started.get() == 1
        assert not task.done()
        client.release[1].set()
        assert await client.started.get() == 2
        assert all(not update.segments and not update.preview for update in updates)
        client.release[0].set()
        client.release[2].set()
        duration, segments = await task
    assert duration == s(12)
    assert [segment.text for segment in segments] == ["0", "1", "2"]
    assert [segment.start for segment in segments] == [s(1), s(5.5), s(10)]
    assert [segment.speaker_id for segment in segments] == ["1:A", "2:A", "3:A"]
    assert client.maximum == 2
    assert updates[-1] == FileTranscriptSnapshot(segments)
    assert [part for stage, part, _ in stages if stage == ImportStage.TRANSCRIBING] == [0, 1, 2, 3]
    assert not any(path.exists() for path in client.uploads)


@run_async
async def test_cancel_drains_active_requests_and_never_starts_queued_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
) -> None:
    use_converter(monkeypatch, FakeConverter(s(12), size=100))
    provider = replace(PROVIDER, upload=UploadSettings(parallelism=2, chunk_seconds=5))
    client = ControlledClient()
    work = tmp_path / "work"
    async with asyncio.timeout(5):
        task = asyncio.create_task(
            transcribe_audio(
                source,
                ModelChoice(provider, MODEL),
                client=client,
                work_dir=work,
            )
        )
        await client.started.get()
        await client.started.get()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert sorted(client.cancelled) == [0, 1]
    assert client.active == 0
    assert len(client.uploads) == 2
    assert list(work.iterdir()) == []


@run_async
async def test_encoded_parts_are_resplit_when_variable_bitrate_exceeds_size_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
) -> None:
    class VariableConverter(FakeConverter):
        async def to_upload(
            self,
            source: Path,
            destination: Path,
            start: timedelta | None = None,
            end: timedelta | None = None,
            format: UploadFormat = OGG_OPUS,
        ) -> timedelta:
            if start is None or end is None:
                write(destination, b"x" * 2000)
                return self.duration
            length = end - start
            write(destination, b"x" * (1500 if length > s(3) else 500))
            return length

    use_converter(monkeypatch, VariableConverter(s(12), size=2000))
    client = FakeClient(max_upload_bytes=1000)
    await transcribe_audio(source, CHOICE, client=client, work_dir=tmp_path / "work")
    assert len(client.uploads) > 2
    assert not any(path.exists() for path in client.uploads)


@run_async
async def test_provider_format_and_size_override_apply_to_every_part(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: Path,
) -> None:
    converter = FakeConverter(s(12), size=2000)
    use_converter(monkeypatch, converter)
    client = FakeClient(max_upload_bytes=5000)
    provider = replace(PROVIDER, upload=UploadSettings(chunk_bytes=1000, format=AudioFormat.FLAC))
    await transcribe_audio(source, ModelChoice(provider, MODEL), client=client, work_dir=tmp_path)
    assert len(client.uploads) >= 2
    assert {path.suffix for path in client.uploads} == {".flac"}


@run_async
async def test_cancel_waits_for_slow_request_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: Path
) -> None:
    use_converter(monkeypatch, FakeConverter(s(12), size=100))
    started: asyncio.Queue[int] = asyncio.Queue()
    cleaned: list[int] = []

    class SlowCleanup(FakeClient):
        async def transcribe_file(
            self,
            audio: Path,
            model: ModelSpec,
            options: TranscriptionOptions,
            *,
            on_update: FileTranscriptCallback | None = None,
        ) -> tuple[Segment, ...]:
            number = len(self.uploads)
            self.uploads.append(audio)
            started.put_nowait(number)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if number == 1:
                    await asyncio.sleep(0.02)
                assert await asyncio.to_thread(audio.exists)
                cleaned.append(number)
                raise
            return ()

    client = SlowCleanup(max_upload_bytes=None)
    provider = replace(PROVIDER, upload=UploadSettings(parallelism=2, chunk_seconds=5))
    async with asyncio.timeout(5):
        task = asyncio.create_task(
            transcribe_audio(
                source,
                ModelChoice(provider, MODEL),
                client=client,
                work_dir=tmp_path / "work",
            )
        )
        await started.get()
        await started.get()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert sorted(cleaned) == [0, 1]
    assert not any(path.exists() for path in client.uploads)
