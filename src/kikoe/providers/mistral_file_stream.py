"""Accumulate Mistral SSE events without treating an interrupted stream as success."""

from mistralai.client.models import (
    TranscriptionStreamDone,
    TranscriptionStreamEvents,
    TranscriptionStreamSegmentDelta,
    TranscriptionStreamTextDelta,
)

from kikoe.core.models import Segment
from kikoe.providers.base import FileTranscript
from kikoe.providers.mistral_parsing import parse_mistral
from kikoe.providers.parsing import parse_text


class MistralFileTranscript(FileTranscript[TranscriptionStreamEvents]):
    """Completed segments replace the text preview; ``transcription.done`` is authoritative."""

    def __init__(self, *, timed: bool, diarize: bool) -> None:
        self._timed = timed
        self._diarize = diarize
        self._segments: list[Segment] = []
        self._preview = ""

    def apply(self, event: TranscriptionStreamEvents) -> bool:
        """Handle one event payload; unknown kinds from newer servers are ignored."""
        match event.data:
            case TranscriptionStreamTextDelta():
                self._preview += event.data.text
                return bool(event.data.text)
            case TranscriptionStreamSegmentDelta():
                self._preview = ""
                self._segments += self._parse({"text": "", "segments": [event.data.model_dump()]})
                return True
            case TranscriptionStreamDone():
                self.commit(self._parse(event.data.model_dump(warnings=False)))
                self._preview = ""
                return True
            case _:
                return False

    def committed(self) -> tuple[Segment, ...]:
        return tuple(self._segments)

    def preview(self) -> tuple[Segment, ...]:
        return parse_text(self._preview)

    def _parse(self, body: object) -> tuple[Segment, ...]:
        return parse_mistral(body, self._timed, self._diarize)
