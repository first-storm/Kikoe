"""Require a successful finish reason before committing chat transcription text."""

from openai.types.chat import ChatCompletionChunk

from kikoe.core.llm_prompt import clean_transcript
from kikoe.core.models import Segment
from kikoe.i18n import _
from kikoe.providers.base import FileTranscript, TranscriptionError
from kikoe.providers.errors import unreadable


def completed_text(
    text: str | None, reason: str | None, host: str, *, allow_empty: bool = False
) -> tuple[Segment, ...]:
    """Partial chat completions must never be saved as complete transcriptions.

    ``reason`` uses the OpenAI names; other APIs map their finish reasons onto them.
    A prompted LLM answers silence with nothing, which ``allow_empty`` accepts.
    """
    match reason:
        case "stop":
            cleaned = clean_transcript(text or "")
            if cleaned:
                return (Segment(cleaned),)
            if allow_empty:
                return ()
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


class ChatFileTranscript(FileTranscript[ChatCompletionChunk]):
    """Text deltas remain previews until the first choice ends with ``stop``."""

    def __init__(self, host: str, *, allow_empty: bool = False) -> None:
        self._host = host
        self._allow_empty = allow_empty
        self._text = ""

    def apply(self, event: ChatCompletionChunk) -> bool:
        """Ignore metadata events and return whether the visible snapshot changed."""
        choice = next((choice for choice in event.choices if choice.index == 0), None)
        if choice is None:
            return False
        return self.add(choice.delta.content or "", choice.finish_reason)

    def add(self, delta: str, finish_reason: str | None) -> bool:
        """Append one delta; return whether the visible snapshot changed."""
        self._text += delta
        if finish_reason is not None:
            self.commit(
                completed_text(self._text, finish_reason, self._host, allow_empty=self._allow_empty)
            )
            return True
        return bool(delta)

    def preview(self) -> tuple[Segment, ...]:
        return (Segment(self._text),) if self._text.strip() else ()
