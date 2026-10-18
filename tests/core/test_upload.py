from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from kikoe.core.models import ApiKind, AudioFormat, UploadSettings
from kikoe.core.provider_registry import descriptor
from kikoe.core.upload import (
    FLAC,
    MP3,
    OGG_OPUS,
    WAV,
    UploadFormat,
    UploadLimits,
    effective_upload_limits,
    upload_format_of,
    validate_upload,
)


@pytest.mark.parametrize("api", list(ApiKind))
def test_defaults_and_documented_maxima_are_valid(api: ApiKind) -> None:
    policy = descriptor(api).upload
    validate_upload(UploadSettings(), api)
    validate_upload(UploadSettings(chunk_bytes=policy.bytes, chunk_seconds=policy.seconds), api)
    with pytest.raises(ValueError, match="size"):
        validate_upload(UploadSettings(chunk_bytes=policy.bytes + 1), api)


@pytest.mark.parametrize("value", [0, -1, 33, True])
def test_invalid_parallelism_is_rejected(value: int) -> None:
    with pytest.raises(ValueError, match="parallel"):
        validate_upload(UploadSettings(parallelism=value), ApiKind.OPENAI)


def test_gemini_duration_and_mimo_format_are_bounded() -> None:
    with pytest.raises(ValueError, match="duration"):
        validate_upload(UploadSettings(chunk_seconds=3601), ApiKind.GEMINI)
    with pytest.raises(ValueError, match="format"):
        validate_upload(UploadSettings(format=AudioFormat.OGG), ApiKind.MIMO)


@pytest.mark.parametrize("field", ["chunk_seconds", "chunk_bytes"])
def test_boolean_limits_are_rejected(field: str) -> None:
    with pytest.raises(ValueError):
        validate_upload(replace(UploadSettings(), **{field: True}), ApiKind.OPENAI)


@pytest.mark.parametrize(
    ("api", "settings", "model", "expected"),
    [
        (ApiKind.OPENAI, UploadSettings(), UploadLimits(), UploadLimits(25_000_000, format=WAV)),
        (
            ApiKind.GEMINI,
            UploadSettings(chunk_bytes=100, chunk_seconds=120, format=AudioFormat.FLAC),
            UploadLimits(80, timedelta(seconds=60)),
            UploadLimits(80, timedelta(seconds=60), FLAC),
        ),
        (
            ApiKind.GEMINI,
            UploadSettings(),
            UploadLimits(duration=timedelta(minutes=30)),
            UploadLimits(2_000_000_000, timedelta(minutes=30)),
        ),
        (
            ApiKind.MIMO,
            UploadSettings(),
            UploadLimits(format=FLAC),
            UploadLimits(7_500_000, format=MP3),
        ),
        (
            ApiKind.DEEPGRAM,
            UploadSettings(chunk_seconds=10),
            UploadLimits(),
            UploadLimits(2_000_000_000, timedelta(seconds=10)),
        ),
    ],
)
def test_effective_limits_preserve_endpoint_and_model_bounds(
    api: ApiKind, settings: UploadSettings, model: UploadLimits, expected: UploadLimits
) -> None:
    assert effective_upload_limits(model, settings, api) == expected


def test_effective_limits_reject_invalid_settings() -> None:
    with pytest.raises(ValueError, match="size"):
        effective_upload_limits(UploadLimits(), UploadSettings(chunk_bytes=0), ApiKind.OPENAI)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.ogg", OGG_OPUS), ("a.flac", FLAC), ("a.wav", WAV), ("a.MP3", MP3)],
)
def test_upload_format_is_read_from_the_chunk_suffix(name: str, expected: UploadFormat) -> None:
    assert upload_format_of(Path(name)) == expected


def test_unknown_chunk_suffix_is_rejected() -> None:
    with pytest.raises(ValueError):
        upload_format_of(Path("a.m4a"))


def test_mp3_is_offered_where_the_api_accepts_it_but_not_for_local_engines() -> None:
    for api in (ApiKind.MIMO, ApiKind.AUDIO_LLM, ApiKind.OPENAI, ApiKind.GEMINI):
        validate_upload(UploadSettings(format=AudioFormat.MP3), api)
    with pytest.raises(ValueError):
        validate_upload(UploadSettings(format=AudioFormat.MP3), ApiKind.LOCAL)
