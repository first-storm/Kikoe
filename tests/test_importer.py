import asyncio
from datetime import timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.chunking import TimeSpan
from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Provider, RecordingSource, Segment
from kikoe.media import transcode
from kikoe.providers.base import TranscriptionError, TranscriptionOptions
from kikoe.services.importer import ImportStage, import_audio
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository

MODEL = ModelSpec("whisper-large-v3", "Whisper", Capabilities(timestamps=True))
PROVIDER = Provider("p1", "Groq", ApiKind.OPENAI_COMPATIBLE, "https://example.test/v1", (MODEL,))


def s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


def write(path: Path, data: bytes) -> None:
    path.write_bytes(data)


class Stages(list[tuple[ImportStage, int, int]]):
    """Progress callback that records every call."""

    def __call__(self, stage: ImportStage, part: int, parts: int) -> None:
        self.append((stage, part, parts))


class FakeClient:
    """Returns one timed segment per upload, numbered in call order."""

    def __init__(self, max_upload_bytes: int | None = 1000, fail: bool = False) -> None:
        self.max_upload_bytes = max_upload_bytes
        self.uploads: list[Path] = []
        self._fail = fail

    async def transcribe_file(
        self, audio: Path, model: ModelSpec, options: TranscriptionOptions
    ) -> tuple[Segment, ...]:
        self.uploads.append(audio)
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
def notes(tmp_path: Path) -> NoteRepository:
    return NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "Team Sync.m4a"
    path.write_bytes(b"original")
    return path


def use_converter(monkeypatch: pytest.MonkeyPatch, converter: FakeConverter) -> None:
    monkeypatch.setattr(transcode, "to_upload", converter.to_upload)
    monkeypatch.setattr(transcode, "find_silences", converter.find_silences)


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
        PROVIDER,
        MODEL,
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
        PROVIDER,
        MODEL,
        client=FakeClient(),
        notes=notes,
        work_dir=tmp_path,
    )
    note = await import_audio(
        source,
        existing.note_id,
        PROVIDER,
        MODEL,
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
        PROVIDER,
        MODEL,
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
            PROVIDER,
            MODEL,
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
        self, audio: Path, model: ModelSpec, options: TranscriptionOptions
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
        import_audio(source, None, PROVIDER, MODEL, client=client, notes=notes, work_dir=tmp_path)
    )
    await client.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert list((tmp_path / "audio").iterdir()) == []
    assert not client.uploads[0].exists()
