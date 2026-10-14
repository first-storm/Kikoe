"""Fixtures shared by several test modules."""

from pathlib import Path

import pytest

from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository


@pytest.fixture
def audio(tmp_path: Path) -> Path:
    """A tiny fake Ogg file; the tests upload it but nothing decodes it."""
    path = tmp_path / "upload.ogg"
    path.write_bytes(b"OggS fake audio")
    return path


@pytest.fixture
def notes(tmp_path: Path) -> NoteRepository:
    """An empty note library in the test's temporary directory."""
    return NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))
