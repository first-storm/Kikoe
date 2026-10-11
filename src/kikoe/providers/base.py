"""The interface every transcription backend implements."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kikoe.core.models import ModelSpec, Segment


@dataclass(frozen=True, slots=True)
class TranscriptionOptions:
    """Per-request switches. Only honoured when the model's Capabilities allow them."""

    timestamps: bool = True
    diarization: bool = True
    language: str | None = None
    """BCP 47 tag, or None to let the service detect it."""


class TranscriptionError(Exception):
    """A provider rejected or failed a request. The message is shown to the user."""


class TranscriptionProvider(Protocol):
    @property
    def max_upload_bytes(self) -> int | None:
        """Largest file :meth:`transcribe_file` accepts; longer audio must be split first."""
        ...

    async def transcribe_file(
        self, audio: Path, model: ModelSpec, options: TranscriptionOptions
    ) -> tuple[Segment, ...]:
        """Upload a finished recording and return its transcript."""
        ...

    # Realtime streaming (for models with Capabilities.realtime) will be added here.
