"""Audio encodings a provider can ask for before upload."""

from dataclasses import dataclass
from datetime import timedelta

from kikoe.core.models import ApiKind, AudioFormat, UploadPolicy, UploadSettings
from kikoe.core.provider_registry import descriptor


@dataclass(frozen=True, slots=True)
class UploadFormat:
    """File suffix and ffmpeg encoder arguments for one upload encoding."""

    suffix: str
    mime: str
    args: tuple[str, ...]


_SPEECH = ("-vn", "-ac", "1", "-ar", "16000")

OGG_OPUS = UploadFormat(".ogg", "audio/ogg", (*_SPEECH, "-c:a", "libopus", "-b:a", "32k"))
"""Default: about 1¾ hours in 25 MB; accepted by OpenAI, Groq and most compatible servers."""

MP3 = UploadFormat(".mp3", "audio/mpeg", (*_SPEECH, "-c:a", "libmp3lame", "-b:a", "32k"))
"""For services that accept only WAV or MP3, such as MiMo."""

FLAC = UploadFormat(".flac", "audio/flac", (*_SPEECH, "-c:a", "flac"))
WAV = UploadFormat(".wav", "audio/wav", (*_SPEECH, "-c:a", "pcm_s16le"))

UPLOAD_FORMATS = {AudioFormat.OGG: OGG_OPUS, AudioFormat.FLAC: FLAC, AudioFormat.WAV: WAV}
MAX_PARALLELISM = 32
MAX_CHUNK_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """Model and option limits, including the encoding accepted by the endpoint."""

    bytes: int | None = None
    duration: timedelta | None = None
    format: UploadFormat = OGG_OPUS


def effective_upload_limits(
    limits: UploadLimits, settings: UploadSettings, api: ApiKind
) -> UploadLimits:
    """Intersect user settings with endpoint and model limits before conversion."""
    validate_upload(settings, api)
    policy = upload_policy(api)
    size = min(settings.chunk_bytes or policy.bytes, limits.bytes or policy.bytes)
    seconds = settings.chunk_seconds or policy.seconds
    duration = timedelta(seconds=seconds) if seconds else limits.duration
    if limits.duration is not None:
        duration = min(duration or limits.duration, limits.duration)
    return UploadLimits(size, duration, UPLOAD_FORMATS[settings.format or policy.format])


def upload_policy(api: ApiKind) -> UploadPolicy:
    """Use the documented limits of the endpoint Kikoe actually calls."""
    return descriptor(api).upload


def validate_upload(settings: UploadSettings, api: ApiKind) -> None:
    """Reject damaged settings and choices exceeding the endpoint caps."""
    policy = upload_policy(api)
    if type(settings.parallelism) is not int or not 1 <= settings.parallelism <= MAX_PARALLELISM:
        raise ValueError("invalid parallel transcription count")
    if settings.chunk_seconds is not None and (
        type(settings.chunk_seconds) is not int
        or not 1 <= settings.chunk_seconds <= (policy.seconds or MAX_CHUNK_SECONDS)
    ):
        raise ValueError("invalid audio chunk duration")
    if settings.chunk_bytes is not None and (
        type(settings.chunk_bytes) is not int or not 1 <= settings.chunk_bytes <= policy.bytes
    ):
        raise ValueError("invalid audio chunk size")
    if settings.format is not None and settings.format not in policy.formats:
        raise ValueError("unsupported upload audio format")
