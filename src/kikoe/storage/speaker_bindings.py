"""Which note speaker each recognition label of a recording stands for, in SQLite.

A binding row maps a provider's speaker label (``source_id``) to a note speaker. A
recording that is new to a note with people of its own waits for a deliberate match.
"""

import sqlite3
import uuid

from kikoe.core.models import Segment


def replace_bindings(
    connection: sqlite3.Connection,
    note_id: str,
    recording_id: str,
    segments: tuple[Segment, ...],
    inherited: dict[str, str] | None = None,
    *,
    replacing: bool = False,
    confirmed_people: set[str] | None = None,
) -> None:
    """Bind every label afresh, keeping the people in ``inherited`` (label → person)."""
    inherited = inherited or {}
    has_people = _others_have_people(connection, note_id, recording_id)
    connection.execute("DELETE FROM recording_speakers WHERE recording_id = ?", (recording_id,))
    for source in _sources(segments):
        original = _insert_speaker(connection, note_id)
        person = inherited.get(source, original)
        confirmed = (
            person in (confirmed_people or set())
            if source in inherited
            else not (has_people or replacing)
        )
        _bind(connection, recording_id, source, person, original, confirmed)


def extend_bindings(
    connection: sqlite3.Connection, note_id: str, recording_id: str, segments: tuple[Segment, ...]
) -> None:
    """Keep bindings of labels already seen; bind new ones as a first save would."""
    sources = _sources(segments)
    bound = {
        row[0]
        for row in connection.execute(
            "SELECT source_id FROM recording_speakers WHERE recording_id = ?", (recording_id,)
        )
    }
    connection.executemany(
        "DELETE FROM recording_speakers WHERE recording_id = ? AND source_id = ?",
        ((recording_id, source) for source in bound - sources.keys()),
    )
    confirmed = not _others_have_people(connection, note_id, recording_id)
    for source in sources:
        if source not in bound:
            original = _insert_speaker(connection, note_id)
            _bind(connection, recording_id, source, original, original, confirmed)


def _sources(segments: tuple[Segment, ...]) -> dict[str, None]:
    return dict.fromkeys(s.speaker_id for s in segments if s.speaker_id is not None)


def _others_have_people(connection: sqlite3.Connection, note_id: str, recording_id: str) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM recording_speakers b JOIN recordings r ON r.id = b.recording_id "
            "WHERE r.note_id = ? AND r.id != ? LIMIT 1",
            (note_id, recording_id),
        ).fetchone()
    )


def _insert_speaker(connection: sqlite3.Connection, note_id: str) -> str:
    """Add an unnamed speaker numbered after the note's others and return its ID."""
    speaker_id = uuid.uuid4().hex
    connection.execute(
        "INSERT INTO speakers (note_id, id, name, ordinal) VALUES (?, ?, '', "
        "(SELECT COALESCE(MAX(ordinal), 0) + 1 FROM speakers WHERE note_id = ?))",
        (note_id, speaker_id, note_id),
    )
    return speaker_id


def _bind(
    connection: sqlite3.Connection,
    recording_id: str,
    source: str,
    person: str,
    original: str,
    confirmed: bool,
) -> None:
    connection.execute(
        "INSERT INTO recording_speakers VALUES (?, ?, ?, ?, ?)",
        (recording_id, source, person, original, confirmed),
    )
