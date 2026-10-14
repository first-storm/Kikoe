"""The note library's SQLite schema, as an ordered list of migrations."""

MIGRATIONS: tuple[str, ...] = (
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
    """
    ALTER TABLE speakers ADD COLUMN ordinal INTEGER NOT NULL DEFAULT 0;
    INSERT OR IGNORE INTO speakers (note_id, id, name)
      SELECT r.note_id, s.speaker_id, '' FROM segments s
      JOIN recordings r ON r.id = s.recording_id
      WHERE s.speaker_id IS NOT NULL ORDER BY r.position, s.position;
    UPDATE speakers SET ordinal = (
      SELECT COUNT(*) FROM speakers other
      WHERE other.note_id = speakers.note_id AND other.rowid <= speakers.rowid);
    CREATE TABLE recording_speakers (
      recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
      source_id TEXT NOT NULL,
      speaker_id TEXT NOT NULL,
      original_id TEXT NOT NULL,
      confirmed INTEGER NOT NULL,
      PRIMARY KEY (recording_id, source_id)
    );
    INSERT INTO recording_speakers
      SELECT DISTINCT r.id, s.speaker_id, s.speaker_id, s.speaker_id,
        CASE WHEN EXISTS (
          SELECT 1 FROM recordings earlier JOIN segments es ON es.recording_id = earlier.id
          WHERE earlier.note_id = r.note_id AND earlier.position < r.position
          AND es.speaker_id IS NOT NULL) THEN 0 ELSE 1 END
      FROM recordings r JOIN segments s ON s.recording_id = r.id
      WHERE s.speaker_id IS NOT NULL;
    """,
)
"""Schema changes in order; entry ``n`` upgrades ``user_version`` from ``n`` to ``n + 1``.

Never edit a released entry; append a new one instead.
"""

SCHEMA_VERSION = len(MIGRATIONS)
