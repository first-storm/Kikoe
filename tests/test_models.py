from dataclasses import FrozenInstanceError, replace

import pytest

from helpers import file_caps
from kikoe.core.export import ExportFormat
from kikoe.core.models import ApiKind, ModelSpec, Provider


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
