"""Render a note as text or subtitles. Display options never change the stored transcript."""

from enum import StrEnum

from kikoe.core.models import DisplayOptions, Note


class ExportFormat(StrEnum):
    TEXT = "txt"
    MARKDOWN = "md"
    SRT = "srt"
    WEBVTT = "vtt"

    @property
    def is_subtitle(self) -> bool:
        """Subtitle formats always include timestamps."""
        return self in {ExportFormat.SRT, ExportFormat.WEBVTT}


def export_note(note: Note, fmt: ExportFormat, options: DisplayOptions) -> str:
    """Return ``note`` rendered in ``fmt``."""
    raise NotImplementedError
