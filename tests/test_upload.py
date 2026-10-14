from dataclasses import replace
from datetime import timedelta

import pytest

from kikoe.core.models import ApiKind, AudioFormat, UploadSettings
from kikoe.core.provider_registry import descriptor
from kikoe.core.upload import (
    FLAC,
    WAV,
    UploadLimits,
    effective_upload_limits,
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
            UploadLimits(7_500_000, format=WAV),
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
