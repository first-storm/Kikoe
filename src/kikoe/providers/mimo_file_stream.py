"""Require a successful MiMo finish reason before committing chat transcription text."""

from openai.types.chat import ChatCompletionChunk

from kikoe.core.models import Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError
from kikoe.providers.errors import unreadable


def completed_text(text: str | None, reason: str | None, host: str) -> tuple[Segment, ...]:
    """Partial chat completions must never be saved as complete transcriptions."""
    match reason:
        case "stop":
            if text and text.strip():
                return (Segment(text.strip()),)
            raise unreadable(host)
        case "length":
            raise TranscriptionError(
                _(
                    "The provider cut the transcription short because of its output limit. "
                    "Reduce the upload chunk length or size in Preferences and try again."
                )
            )
        case "content_filter":
            raise TranscriptionError(
                _("The provider blocked the transcription with a content filter.")
            )
        case _:
            raise unreadable(host)


class MiMoFileTranscript:
    """Chat deltas remain previews until the first choice ends with ``stop``."""

    def __init__(self, host: str) -> None:
        self._host = host
        self._text = ""
        self._result: tuple[Segment, ...] | None = None

    def apply(self, event: ChatCompletionChunk) -> bool:
        """Ignore metadata events and return whether the visible snapshot changed."""
        choice = next((choice for choice in event.choices if choice.index == 0), None)
        if choice is None:
            return False
        delta = choice.delta.content or ""
        self._text += delta
        if choice.finish_reason is not None:
            self._result = completed_text(self._text, choice.finish_reason, self._host)
            return True
        return bool(delta)

    @property
    def finished(self) -> bool:
        return self._result is not None

    @property
    def snapshot(self) -> FileTranscriptSnapshot:
        if self._result is not None:
            return FileTranscriptSnapshot(self._result)
        preview = (Segment(self._text),) if self._text.strip() else ()
        return FileTranscriptSnapshot(preview=preview)

    def result(self) -> tuple[Segment, ...]:
        """A clean SSE EOF alone does not prove the transcription is complete."""
        if self._result is None:
            raise TranscriptionError(
                _("Transcription ended before it was complete. Try again."), retryable=True
            )
        return self._result
