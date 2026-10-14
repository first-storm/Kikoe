"""Reading rows of the note library into domain objects, and the encodings they use."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from kikoe.core.models import (
    DisplayOptions,
    Note,
    Recording,
    RecordingSource,
    Segment,
    Speaker,
    SpeakerBinding,
    TranscriptionState,
)
from kikoe.i18n import _
from kikoe.storage.errors import NoteNotFoundError


def require_found(rowcount: int) -> None:
    if rowcount == 0:
        raise note_not_found()


def note_not_found() -> NoteNotFoundError:
    return NoteNotFoundError(_("This note no longer exists."))


def format_time(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text)


def to_ms(duration: timedelta) -> int:
    return round(duration / timedelta(milliseconds=1))


def from_ms(milliseconds: int) -> timedelta:
    return timedelta(milliseconds=milliseconds)


def read_note(connection: sqlite3.Connection, note_id: str, audio_dir: Path) -> Note:
    """Load one note with its speakers, recordings, segments and speaker assignments."""
    row = connection.execute(
        "SELECT title, created_at, show_timestamps, show_speakers FROM notes WHERE id = ?",
        (note_id,),
    ).fetchone()
    if row is None:
        raise note_not_found()
    title, created_at, show_timestamps, show_speakers = row
    speakers = tuple(
        Speaker(speaker_id, name, ordinal)
        for speaker_id, name, ordinal in connection.execute(
            "SELECT id, name, ordinal FROM speakers WHERE note_id = ? ORDER BY ordinal",
            (note_id,),
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
                None if start_ms is None else from_ms(start_ms),
                None if end_ms is None else from_ms(end_ms),
                speaker_id,
            )
        )
    recordings = tuple(
        Recording(
            recording_id=recording_id,
            created_at=parse_time(recording_created_at),
            duration=from_ms(duration_ms),
            source=RecordingSource(source),
            provider_name=provider_name,
            model_id=model_id,
            segments=tuple(segments.get(recording_id, ())),
            audio_path=None if audio_file is None else audio_dir / audio_file,
            transcription_state=TranscriptionState(transcription_state),
            speaker_bindings=tuple(
                SpeakerBinding(source, person, original, bool(confirmed))
                for source, person, original, confirmed in connection.execute(
                    "SELECT source_id, speaker_id, original_id, confirmed "
                    "FROM recording_speakers WHERE recording_id = ? ORDER BY rowid",
                    (recording_id,),
                )
            ),
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
        created_at=parse_time(created_at),
        recordings=recordings,
        speakers=speakers,
        display=DisplayOptions(bool(show_timestamps), bool(show_speakers)),
    )
