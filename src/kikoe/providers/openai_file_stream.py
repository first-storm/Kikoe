"""Accumulate SDK file events without treating an interrupted stream as success."""

from openai.types.audio import TranscriptionStreamEvent

from kikoe.core.models import Segment
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError
from kikoe.providers.parsing import parse_segments


class OpenAIFileTranscript:
    """Completed diarized segments replace previews with the same provider ID."""

    def __init__(self, *, structured: bool, timed: bool, diarize: bool) -> None:
        self._structured = structured
        self._timed = timed
        self._diarize = diarize
        self._segments: dict[str, tuple[Segment, ...]] = {}
        self._starts: dict[str, float] = {}
        self._previews: dict[str, str] = {}
        self._text: str | None = None

    def apply(self, event: TranscriptionStreamEvent) -> None:
        """Keep provider text intact until its authoritative completion arrives."""
        match event.type:
            case "transcript.text.delta":
                id = event.segment_id or "text"
                if id not in self._segments:
                    self._previews[id] = self._previews.get(id, "") + event.delta
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
            case "transcript.text.done":
                self._text = event.text
                self._previews.clear()
                # Validate before a final-looking snapshot reaches the UI.
                self.result()
            case _:
                raise ValueError("Unknown transcription stream event")

    @property
    def snapshot(self) -> FileTranscriptSnapshot:
        segments = tuple(
            s
            for id in sorted(self._segments, key=self._starts.__getitem__)
            for s in self._segments[id]
        )
        if self._text is not None and not self._structured:
            segments = parse_segments({"text": self._text}, False, False)
        return FileTranscriptSnapshot(
            segments, tuple(Segment(text) for text in self._previews.values() if text.strip())
        )

    def result(self) -> tuple[Segment, ...]:
        """EOF is not a completion event, even when the SDK ends normally."""
        if self._text is None:
            raise TranscriptionError(
                _("Transcription ended before it was complete. Try again."), retryable=True
            )
        if self._structured and self._text.strip() and not self.snapshot.segments:
            raise ValueError("Missing diarized segments")
        return self.snapshot.segments
