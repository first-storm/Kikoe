from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from helpers import file_caps
from kikoe.core.export import ExportFormat
from kikoe.core.models import (
    ApiKind,
    ModelSpec,
    Note,
    Provider,
    Recording,
    RecordingSource,
    Speaker,
)


def test_provider_is_immutable() -> None:
    provider = Provider("p1", "Groq", ApiKind.OPENAI_COMPATIBLE, "https://api.groq.com/openai/v1")
    with pytest.raises(FrozenInstanceError):
        provider.name = "Other"  # type: ignore[misc]


def test_replace_returns_updated_copy() -> None:
    model = ModelSpec("whisper-1", "whisper-1", file_caps(timestamps=True))
    provider = Provider("p1", "OpenAI", ApiKind.OPENAI, "https://api.openai.com/v1")

    updated = replace(provider, models=(*provider.models, model))

    assert provider.models == ()
    assert updated.models == (model,)


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (ExportFormat.TEXT, False),
        (ExportFormat.MARKDOWN, False),
        (ExportFormat.SRT, True),
        (ExportFormat.WEBVTT, True),
    ],
)
def test_subtitle_formats(fmt: ExportFormat, expected: bool) -> None:
    assert fmt.is_subtitle is expected


def _note() -> Note:
    recording = Recording(
        "r1", datetime(2026, 1, 1, tzinfo=UTC), timedelta(), RecordingSource.IMPORTED, "P", "m"
    )
    return Note(
        "n1",
        "Note",
        datetime(2026, 1, 1, tzinfo=UTC),
        recordings=(recording,),
        speakers=(Speaker("s1", "Ana", 1),),
    )


def test_note_finds_recording_and_speaker_by_id() -> None:
    note = _note()

    assert note.recording("r1") is note.recordings[0]
    assert note.speaker("s1") is note.speakers[0]
    assert note.speaker_name("s1") == "Ana"


def test_note_lookups_return_none_for_unknown_ids() -> None:
    note = _note()

    assert note.recording("missing") is None
    assert note.speaker("missing") is None
    assert note.speaker(None) is None
    assert note.speaker_name("missing") is None
