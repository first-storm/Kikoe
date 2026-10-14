"""Accumulate Mistral SSE events without treating an interrupted stream as success."""

from mistralai.client.models import (
    TranscriptionStreamDone,
    TranscriptionStreamSegmentDelta,
    TranscriptionStreamTextDelta,
)

from kikoe.core.models import Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError
from kikoe.providers.mistral_parsing import parse_mistral
from kikoe.providers.parsing import parse_text


class MistralFileTranscript:
    """Completed segments replace the text preview; ``transcription.done`` is authoritative."""

    def __init__(self, *, timed: bool, diarize: bool, words_only: bool) -> None:
        self._timed = timed
        self._diarize = diarize
        self._words_only = words_only
        self._segments: list[Segment] = []
        self._preview = ""
        self._done: tuple[Segment, ...] | None = None

    def apply(self, event: object) -> None:
        """Handle one event payload; unknown kinds from newer servers are ignored."""
        match event:
            case TranscriptionStreamTextDelta():
                self._preview += event.text
            case TranscriptionStreamSegmentDelta():
                # Word-level entries are grouped into sentences only once the text is complete.
                if self._words_only:
                    return
                self._preview = ""
                self._segments += self._parse({"text": "", "segments": [event.model_dump()]})
            case TranscriptionStreamDone():
                self._done = self._parse(event.model_dump(warnings=False))
                self._preview = ""
            case _:
                pass

    def _parse(self, body: object) -> tuple[Segment, ...]:
        return parse_mistral(body, self._timed, self._diarize, self._words_only)

    @property
    def finished(self) -> bool:
        return self._done is not None

    @property
    def snapshot(self) -> FileTranscriptSnapshot:
        segments = self._done if self._done is not None else tuple(self._segments)
        return FileTranscriptSnapshot(segments, parse_text(self._preview))

    def result(self) -> tuple[Segment, ...]:
        """EOF is not a completion event, even when the SDK ends normally."""
        if self._done is None:
            raise TranscriptionError(
                _("Transcription ended before it was complete. Try again."), retryable=True
            )
        return self._done
