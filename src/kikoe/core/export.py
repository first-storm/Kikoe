"""Render a note as text or subtitles. Display options never change the stored transcript."""

from dataclasses import dataclass
from enum import StrEnum

from kikoe.core.models import Note


class ExportFormat(StrEnum):
    TEXT = "txt"
    MARKDOWN = "md"
    SRT = "srt"
    WEBVTT = "vtt"

    @property
    def is_subtitle(self) -> bool:
        """Subtitle formats always include timestamps."""
        return self in {ExportFormat.SRT, ExportFormat.WEBVTT}


@dataclass(frozen=True, slots=True)
class ExportOptions:
    show_timestamps: bool = True
    show_speakers: bool = True


def export_note(note: Note, fmt: ExportFormat, options: ExportOptions) -> str:
    """Return ``note`` rendered in ``fmt``."""
    raise NotImplementedError
