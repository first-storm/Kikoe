"""Audio encodings a provider can ask for before upload."""

from dataclasses import dataclass


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
