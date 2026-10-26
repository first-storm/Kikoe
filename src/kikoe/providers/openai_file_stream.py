"""Accumulate SDK file events without treating an interrupted stream as success."""

from openai.types.audio import TranscriptionStreamEvent

from kikoe.core.models import Segment
from kikoe.providers.base import FileTranscript
from kikoe.providers.parsing import parse_segments, parse_text


class OpenAIFileTranscript(FileTranscript[TranscriptionStreamEvent]):
    """Completed diarized segments replace previews with the same provider ID."""

    def __init__(self, *, structured: bool, timed: bool, diarize: bool) -> None:
        self._structured = structured
        self._timed = timed
        self._diarize = diarize
        self._segments: dict[str, tuple[Segment, ...]] = {}
        self._starts: dict[str, float] = {}
        self._previews: dict[str, str] = {}
        self._text: str | None = None

    def apply(self, event: TranscriptionStreamEvent) -> bool:
        """Keep provider text intact until its authoritative completion arrives."""
        match event.type:
            case "transcript.text.delta":
                id = event.segment_id or "text"
                if id in self._segments:
                    return False
                self._previews[id] = self._previews.get(id, "") + event.delta
                return bool(event.delta)
            case "transcript.text.segment":
                if not self._structured:
                    raise ValueError("Unexpected diarized segment")
                self._segments[event.id] = parse_segments(
                    {"segments": [event.model_dump()], "text": ""},
                    self._timed,
                    self._diarize,
                )
                self._starts[event.id] = event.start
                self._previews.pop(event.id, None)
                return True
            case "transcript.text.done":
                self._text = event.text
                self._previews.clear()
                # Validate before a final-looking snapshot reaches the UI.
                self.commit(self._final())
                return True
            case _:
                raise ValueError("Unknown transcription stream event")

    def committed(self) -> tuple[Segment, ...]:
        return tuple(
            s
            for id in sorted(self._segments, key=self._starts.__getitem__)
            for s in self._segments[id]
        )

    def preview(self) -> tuple[Segment, ...]:
        return tuple(Segment(text) for text in self._previews.values() if text.strip())

    def _final(self) -> tuple[Segment, ...]:
        segments = self.committed() if self._structured else parse_text(self._text or "")
        if self._structured and (self._text or "").strip() and not segments:
            raise ValueError("Missing diarized segments")
        return segments
