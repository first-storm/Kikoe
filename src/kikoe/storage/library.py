"""The note library: an SQLite database plus retained audio files in the user data dir.

Blocking; call it via ``asyncio.to_thread``. Every call opens its own connection, so it
does not matter which worker thread runs it.
"""

import logging
import os
import shutil
import sqlite3
import uuid
from collections.abc import Generator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kikoe import paths
from kikoe.core.models import (
    DisplayOptions,
    Note,
    NoteSummary,
    Recording,
    RecordingSource,
    Segment,
    Speaker,
    TranscriptionState,
)
from kikoe.i18n import _

log = logging.getLogger(__name__)

DATABASE_NAME = "library.sqlite3"
AUDIO_DIR_NAME = "audio"

_MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE notes (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        show_timestamps INTEGER NOT NULL DEFAULT 1,
        show_speakers INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE speakers (
        note_id TEXT NOT NULL REFERENCES notes (id) ON DELETE CASCADE,
        id TEXT NOT NULL,
        name TEXT NOT NULL,
        PRIMARY KEY (note_id, id)
    );
    CREATE TABLE recordings (
        id TEXT PRIMARY KEY,
        note_id TEXT NOT NULL REFERENCES notes (id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        duration_ms INTEGER NOT NULL,
        source TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        model_id TEXT NOT NULL,
        audio_file TEXT
    );
    CREATE INDEX recordings_by_note ON recordings (note_id, position);
    CREATE TABLE segments (
        recording_id TEXT NOT NULL REFERENCES recordings (id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        start_ms INTEGER,
        end_ms INTEGER,
        speaker_id TEXT,
        text TEXT NOT NULL,
        PRIMARY KEY (recording_id, position)
    );
    """,
    "ALTER TABLE recordings ADD COLUMN transcription_state TEXT NOT NULL DEFAULT 'ready';",
)
"""Schema changes in order; entry ``n`` upgrades ``user_version`` from ``n`` to ``n + 1``.

Never edit a released entry; append a new one instead.
"""

SCHEMA_VERSION = len(_MIGRATIONS)


class LibraryError(Exception):
    """The library cannot be read or written. The message is meant for the user."""


class NoteNotFoundError(LookupError):
    """A note was deleted while something still referred to it."""


def database_path() -> Path:
    return paths.data_dir() / DATABASE_NAME


def audio_dir() -> Path:
    return paths.data_dir() / AUDIO_DIR_NAME


class Library:
    def __init__(self, database: Path, audio_dir: Path) -> None:
        self._database = database
        self._audio_dir = audio_dir

    def migrate(self) -> None:
        """Bring the schema up to date. Call once before anything else."""
        with self._connect() as connection:
            version: int = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise LibraryError(
                    _(
                        "The note library was created by a newer version of Kikoe. "
                        "Update Kikoe to open it."
                    )
                )
            for number in range(version, SCHEMA_VERSION):
                connection.executescript(
                    f"BEGIN;\n{_MIGRATIONS[number]}\nPRAGMA user_version = {number + 1};\nCOMMIT;"
                )
                log.info("Migrated library to version %d", number + 1)

    def list_notes(self) -> tuple[NoteSummary, ...]:
        """All notes, newest first."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT n.id, n.title, n.created_at, COUNT(r.id), COALESCE(SUM(r.duration_ms), 0),
                    (SELECT source FROM recordings WHERE note_id = n.id
                     ORDER BY position LIMIT 1)
                FROM notes AS n LEFT JOIN recordings AS r ON r.note_id = n.id
                GROUP BY n.id
                ORDER BY n.created_at DESC
                """
            ).fetchall()
        return tuple(
            NoteSummary(
                note_id=note_id,
                title=title,
                created_at=_parse_time(created_at),
                recording_count=count,
                duration=_from_ms(duration_ms),
                source=None if source is None else RecordingSource(source),
            )
            for note_id, title, created_at, count, duration_ms, source in rows
        )

    def get_note(self, note_id: str) -> Note:
        with self._connect() as connection:
            return self._read_note(connection, note_id)

    def find_recording_note(self, recording_id: str) -> Note | None:
        """Identify a previously committed recovery even if its original note was deleted."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT note_id FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
            return self._read_note(connection, row[0]) if row is not None else None

    def create_note(self, title: str, now: datetime) -> Note:
        note = Note(uuid.uuid4().hex, title, now)
        with self._connect() as connection, connection:
            _insert_note(connection, note)
        return note

    def rename_note(self, note_id: str, title: str) -> None:
        with self._connect() as connection, connection:
            cursor = connection.execute("UPDATE notes SET title = ? WHERE id = ?", (title, note_id))
            _require_found(cursor.rowcount)

    def set_display_options(self, note_id: str, options: DisplayOptions) -> None:
        with self._connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE notes SET show_timestamps = ?, show_speakers = ? WHERE id = ?",
                (options.show_timestamps, options.show_speakers, note_id),
            )
            _require_found(cursor.rowcount)

    def delete_note(self, note_id: str) -> None:
        """Delete the note, its transcript and its retained audio files."""
        with self._connect() as connection, connection:
            files = [
                name
                for (name,) in connection.execute(
                    "SELECT audio_file FROM recordings WHERE note_id = ? AND audio_file NOT NULL",
                    (note_id,),
                )
            ]
            cursor = connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            _require_found(cursor.rowcount)
        for name in files:
            self.discard_audio(self._audio_dir / name)

    def store_audio(self, source: Path, recording_id: str) -> Path:
        """Copy an original into the library so it outlives the file the user picked."""
        destination = self._audio_dir / f"{recording_id}{source.suffix.lower()}"
        try:
            self._audio_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            with destination.open("rb") as audio:
                os.fsync(audio.fileno())
        except OSError as error:
            raise LibraryError(
                _("Could not copy the audio file into the library: {error}").format(error=error)
            ) from error
        return destination

    def discard_audio(self, path: Path) -> None:
        """Remove a retained audio file; failure only costs disk space, so it is logged."""
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            log.warning("Could not delete %s: %s", path, error)

    def add_recording(self, note_id: str | None, title: str, recording: Recording) -> Note:
        """Append ``recording`` to a note, or to a new note called ``title`` if ``note_id`` is None.

        Everything is written in one transaction, so a failure leaves no partial transcript.
        """
        with self._connect() as connection:
            with connection:
                if note_id is None:
                    note_id = uuid.uuid4().hex
                    _insert_note(connection, Note(note_id, title, recording.created_at))
                elif not connection.execute(
                    "SELECT 1 FROM notes WHERE id = ?", (note_id,)
                ).fetchone():
                    raise _note_not_found()
                self._insert_recording(connection, note_id, recording)
            return self._read_note(connection, note_id)

    def _insert_recording(
        self, connection: sqlite3.Connection, note_id: str, recording: Recording
    ) -> None:
        audio_file = None
        if recording.audio_path is not None:
            audio_file = recording.audio_path.relative_to(self._audio_dir).as_posix()
        connection.execute(
            """
            INSERT INTO recordings (id, note_id, position, created_at, duration_ms, source,
                provider_name, model_id, audio_file, transcription_state)
            VALUES (?, ?, (SELECT COALESCE(MAX(position) + 1, 0) FROM recordings
                WHERE note_id = ?), ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                recording.recording_id,
                note_id,
                note_id,
                _format_time(recording.created_at),
                _to_ms(recording.duration),
                recording.source.value,
                recording.provider_name,
                recording.model_id,
                audio_file,
                recording.transcription_state.value,
            ),
        )
        self._insert_segments(connection, recording.recording_id, recording.segments)

    def update_transcription(
        self,
        note_id: str,
        recording_id: str,
        state: TranscriptionState,
        *,
        segments: tuple[Segment, ...] = (),
        provider_name: str | None = None,
        model_id: str | None = None,
    ) -> Note:
        """Commit a retry to the same recording, including a successful empty transcript."""
        with self._connect() as connection, connection:
            cursor = connection.execute(
                "UPDATE recordings SET transcription_state = ?, "
                "provider_name = COALESCE(?, provider_name), model_id = COALESCE(?, model_id) "
                "WHERE id = ? AND note_id = ?",
                (state.value, provider_name, model_id, recording_id, note_id),
            )
            _require_found(cursor.rowcount)
            if state is TranscriptionState.READY:
                connection.execute("DELETE FROM segments WHERE recording_id = ?", (recording_id,))
                self._insert_segments(connection, recording_id, segments)
            return self._read_note(connection, note_id)

    def _insert_segments(
        self, connection: sqlite3.Connection, recording_id: str, segments: tuple[Segment, ...]
    ) -> None:
        connection.executemany(
            """
            INSERT INTO segments (recording_id, position, start_ms, end_ms, speaker_id, text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    recording_id,
                    position,
                    None if segment.start is None else _to_ms(segment.start),
                    None if segment.end is None else _to_ms(segment.end),
                    segment.speaker_id,
                    segment.text,
                )
                for position, segment in enumerate(segments)
            ),
        )

    def _read_note(self, connection: sqlite3.Connection, note_id: str) -> Note:
        row = connection.execute(
            "SELECT title, created_at, show_timestamps, show_speakers FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row is None:
            raise _note_not_found()
        title, created_at, show_timestamps, show_speakers = row
        speakers = tuple(
            Speaker(speaker_id, name)
            for speaker_id, name in connection.execute(
                "SELECT id, name FROM speakers WHERE note_id = ? ORDER BY rowid", (note_id,)
            )
        )
        segments: dict[str, list[Segment]] = {}
        for recording_id, start_ms, end_ms, speaker_id, text in connection.execute(
            """
            SELECT s.recording_id, s.start_ms, s.end_ms, s.speaker_id, s.text
            FROM segments AS s JOIN recordings AS r ON r.id = s.recording_id
            WHERE r.note_id = ?
            ORDER BY s.recording_id, s.position
            """,
            (note_id,),
        ):
            segments.setdefault(recording_id, []).append(
                Segment(
                    text,
                    None if start_ms is None else _from_ms(start_ms),
                    None if end_ms is None else _from_ms(end_ms),
                    speaker_id,
                )
            )
        recordings = tuple(
            Recording(
                recording_id=recording_id,
                created_at=_parse_time(recording_created_at),
                duration=_from_ms(duration_ms),
                source=RecordingSource(source),
                provider_name=provider_name,
                model_id=model_id,
                segments=tuple(segments.get(recording_id, ())),
                audio_path=None if audio_file is None else self._audio_dir / audio_file,
                transcription_state=TranscriptionState(transcription_state),
            )
            for (
                recording_id,
                recording_created_at,
                duration_ms,
                source,
                provider_name,
                model_id,
                audio_file,
                transcription_state,
            ) in connection.execute(
                """
                SELECT id, created_at, duration_ms, source, provider_name, model_id, audio_file,
                    transcription_state
                FROM recordings WHERE note_id = ? ORDER BY position
                """,
                (note_id,),
            )
        )
        return Note(
            note_id=note_id,
            title=title,
            created_at=_parse_time(created_at),
            recordings=recordings,
            speakers=speakers,
            display=DisplayOptions(bool(show_timestamps), bool(show_speakers)),
        )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        try:
            with closing(sqlite3.connect(self._database)) as connection:
                connection.execute("PRAGMA foreign_keys = ON")
                yield connection
        except sqlite3.Error as error:
            raise LibraryError(
                _("The note library could not be read or saved: {error}").format(error=error)
            ) from error


def _insert_note(connection: sqlite3.Connection, note: Note) -> None:
    connection.execute(
        """
        INSERT INTO notes (id, title, created_at, show_timestamps, show_speakers)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            note.note_id,
            note.title,
            _format_time(note.created_at),
            note.display.show_timestamps,
            note.display.show_speakers,
        ),
    )


def _require_found(rowcount: int) -> None:
    if rowcount == 0:
        raise _note_not_found()


def _note_not_found() -> NoteNotFoundError:
    return NoteNotFoundError(_("This note no longer exists."))


def _format_time(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def _parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _to_ms(duration: timedelta) -> int:
    return round(duration / timedelta(milliseconds=1))


def _from_ms(milliseconds: int) -> timedelta:
    return timedelta(milliseconds=milliseconds)
