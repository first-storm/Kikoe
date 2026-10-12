"""Durable PCM journals survive crashes before a recording reaches the note library."""

import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

from kikoe import paths
from kikoe.core.models import Segment
from kikoe.i18n import _


class CaptureStoreError(Exception):
    """A recovery journal could not be read or saved; files are left intact."""


@dataclass(frozen=True, slots=True)
class CaptureJournal:
    """Enough information to recover audio, without credentials or an upload request."""

    recording_id: str
    note_id: str
    title: str
    created_at: datetime
    sample_rate: int
    provider_name: str
    model_id: str


def capture_dir() -> Path:
    """Recovery files belong in durable data, never the disposable cache."""
    return paths.data_dir() / "pending-recordings"


class CaptureStore:
    """Blocking journal I/O; callers use asyncio.to_thread and one writer per session."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._last_sync: dict[str, float] = {}

    def pcm_path(self, journal: CaptureJournal) -> Path:
        return self.directory / f"{journal.recording_id}.pcm"

    def flac_path(self, journal: CaptureJournal) -> Path:
        return self.directory / f"{journal.recording_id}.flac"

    def create(self, journal: CaptureJournal) -> None:
        """Persist metadata before accepting PCM so every audio block has a recovery target."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            data = asdict(journal)
            data["created_at"] = journal.created_at.isoformat()
            data["version"] = 1
            destination = self.directory / f"{journal.recording_id}.json"
            temporary = destination.with_suffix(".tmp")
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(data, file)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(destination)
        except OSError as error:
            raise _storage_error(error) from error

    def append(self, journal: CaptureJournal, chunk: bytes) -> None:
        """Flush every block and fsync at least once per second during capture."""
        try:
            with self.pcm_path(journal).open("ab") as file:
                file.write(chunk)
                file.flush()
                now = time.monotonic()
                if now - self._last_sync.get(journal.recording_id, 0) >= 1:
                    os.fsync(file.fileno())
                    self._last_sync[journal.recording_id] = now
        except OSError as error:
            raise _storage_error(error) from error

    def read_pcm(self, journal: CaptureJournal, offset: int, size: int) -> bytes:
        """Read only accepted samples; the network reader never owns the capture writer."""
        try:
            with self.pcm_path(journal).open("rb") as file:
                file.seek(offset)
                return file.read(size)
        except FileNotFoundError:
            return b""  # Capture metadata is created before the first PCM block.
        except OSError as error:
            raise _storage_error(error) from error

    def checkpoint(self, journal: CaptureJournal, segments: tuple[Segment, ...]) -> None:
        """Only final text is atomically synced; recovery must never promote a preview."""
        path = self.directory / f"{journal.recording_id}.transcript"
        temporary = path.with_suffix(".transcript.tmp")
        data = {
            "version": 1,
            "segments": [
                {
                    "text": s.text,
                    "start": s.start.total_seconds() if s.start is not None else None,
                    "end": s.end.total_seconds() if s.end is not None else None,
                    "speaker": s.speaker_id,
                }
                for s in segments
            ],
        }
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(data, file)
                file.flush()
                os.fsync(file.fileno())
            temporary.replace(path)
        except OSError as error:
            raise _storage_error(error) from error

    def transcript(self, journal: CaptureJournal) -> tuple[Segment, ...]:
        """Existing M4 journals have no transcript sidecar and remain recoverable."""
        path = self.directory / f"{journal.recording_id}.transcript"
        try:
            data: object = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Invalid transcript checkpoint")
            payload = cast(dict[str, object], data)
            raw = payload.get("segments")
            if payload.get("version") != 1 or not isinstance(raw, list):
                raise ValueError("Unsupported transcript checkpoint")
            segments: list[Segment] = []
            for item in cast(list[object], raw):
                if not isinstance(item, dict):
                    raise ValueError("Invalid segment")
                fields = cast(dict[str, object], item)
                speaker = fields.get("speaker")
                if speaker is not None and not isinstance(speaker, str):
                    raise ValueError("Invalid speaker")
                segments.append(
                    Segment(
                        _text(fields, "text"),
                        _offset(fields.get("start")),
                        _offset(fields.get("end")),
                        speaker,
                    )
                )
            return tuple(segments)
        except FileNotFoundError:
            return ()
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CaptureStoreError(
                _("Could not read the saved transcript. Its audio has been kept.")
            ) from error

    def finish(self, journal: CaptureJournal) -> timedelta:
        """Discard only an incomplete final sample, then sync before conversion or exit."""
        try:
            path = self.pcm_path(journal)
            if not path.exists():
                return timedelta()
            with path.open("r+b") as file:
                size = path.stat().st_size
                size -= size % 2
                file.truncate(size)
                file.flush()
                os.fsync(file.fileno())
            return timedelta(seconds=size / (2 * journal.sample_rate))
        except OSError as error:
            raise _storage_error(error) from error

    def pending(self) -> tuple[CaptureJournal, ...]:
        """Read journals without altering unknown versions or damaged metadata."""
        try:
            return tuple(self._load(path) for path in sorted(self.directory.glob("*.json")))
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CaptureStoreError(
                _("Could not read a saved recording. Its audio has been kept. ({detail})").format(
                    detail=error
                )
            ) from error

    def _load(self, path: Path) -> CaptureJournal:
        data: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Invalid recording journal")
        fields = cast(dict[str, object], data)
        if fields.get("version") != 1:
            raise ValueError("Unsupported recording journal version")
        recording_id = _text(fields, "recording_id")
        if len(recording_id) != 32 or any(c not in "0123456789abcdef" for c in recording_id):
            raise ValueError("Invalid recording identifier")
        if path.stem != recording_id:
            raise ValueError("Recording identifier does not match its file")
        rate = fields.get("sample_rate")
        if not isinstance(rate, int) or isinstance(rate, bool) or not 0 < rate <= 384000:
            raise ValueError("Invalid sample rate")
        created = datetime.fromisoformat(_text(fields, "created_at"))
        if created.tzinfo is None:
            raise ValueError("Recording date has no time zone")
        return CaptureJournal(
            recording_id,
            _text(fields, "note_id"),
            _text(fields, "title"),
            created,
            rate,
            _text(fields, "provider_name"),
            _text(fields, "model_id"),
        )

    def discard(self, journal: CaptureJournal) -> None:
        """Remove a journal only after its audio and library entry are durable."""
        try:
            self.pcm_path(journal).unlink(missing_ok=True)
            self.flac_path(journal).unlink(missing_ok=True)
            (self.directory / f"{journal.recording_id}.transcript").unlink(missing_ok=True)
            (self.directory / f"{journal.recording_id}.transcript.tmp").unlink(missing_ok=True)
            (self.directory / f"{journal.recording_id}.json").unlink(missing_ok=True)
            self._last_sync.pop(journal.recording_id, None)
        except OSError as error:
            raise _storage_error(error) from error


def _text(fields: dict[str, object], key: str) -> str:
    value = fields[key]
    if not isinstance(value, str):
        raise ValueError(f"Invalid {key}")
    return value


def _storage_error(error: OSError) -> CaptureStoreError:
    return CaptureStoreError(_("Could not save the recording. ({detail})").format(detail=error))


def _offset(value: object) -> timedelta | None:
    import math

    if value is None:
        return None
    if (
        not isinstance(value, int | float)
        or isinstance(value, bool)
        or value < 0
        or not math.isfinite(value)
    ):
        raise ValueError("Invalid segment time")
    return timedelta(seconds=value)
