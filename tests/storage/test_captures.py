"""Capture journals preserve recoverable audio when metadata is damaged."""

from datetime import timedelta
from pathlib import Path

import pytest

from kikoe.storage.captures import CaptureStore, CaptureStoreError
from tests.helpers import journal


@pytest.fixture
def store(tmp_path: Path) -> CaptureStore:
    return CaptureStore(tmp_path / "pending")


def test_journal_reopens_and_repairs_only_an_incomplete_sample(store: CaptureStore) -> None:
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0" * 32000 + b"x")
    reopened = CaptureStore(store.directory)
    assert reopened.pending() == (entry,)
    assert reopened.finish(entry) == timedelta(seconds=1)
    assert reopened.pcm_path(entry).stat().st_size == 32000
    assert "api_key" not in (store.directory / f"{entry.recording_id}.json").read_text()


@pytest.mark.parametrize(
    "content", ['{"version": 2}', '{"version": 1, "recording_id": "../escape"}', "broken"]
)
def test_damaged_journal_is_reported_without_deleting_audio(
    store: CaptureStore, content: str
) -> None:
    entry = journal()
    store.create(entry)
    store.append(entry, b"\0\0")
    metadata = store.directory / f"{entry.recording_id}.json"
    metadata.write_text(content)
    with pytest.raises(CaptureStoreError):
        store.pending()
    assert store.pcm_path(entry).read_bytes() == b"\0\0"
    assert metadata.read_text() == content


def test_damaged_live_checkpoint_keeps_pcm_for_recovery(store: CaptureStore) -> None:
    entry = journal()
    store.create(entry)
    store.append(entry, bytes(32000))
    (store.directory / f"{entry.recording_id}.transcript").write_text('{"version": 99}')
    with pytest.raises(CaptureStoreError, match="audio has been kept"):
        store.transcript(entry)
    assert store.pcm_path(entry).read_bytes() == bytes(32000)
