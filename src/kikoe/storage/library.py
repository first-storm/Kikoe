"""The note library: an SQLite database plus retained audio files in the user data dir."""

from pathlib import Path

from kikoe import paths

DATABASE_NAME = "library.sqlite3"
AUDIO_DIR_NAME = "audio"


def database_path() -> Path:
    return paths.data_dir() / DATABASE_NAME


def audio_dir() -> Path:
    path = paths.data_dir() / AUDIO_DIR_NAME
    path.mkdir(exist_ok=True)
    return path
