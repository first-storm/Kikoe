"""GObject wrappers so core dataclasses can live in a ``Gio.ListStore``."""

from gi.repository import GObject

from kikoe.core.models import ModelSpec, NoteSummary, Provider, Recording, Segment


class ProviderItem(GObject.Object):
    def __init__(self, provider: Provider) -> None:
        super().__init__()
        self.provider = provider


class ModelItem(GObject.Object):
    def __init__(self, model: ModelSpec) -> None:
        super().__init__()
        self.model = model


class NoteItem(GObject.Object):
    def __init__(self, summary: NoteSummary) -> None:
        super().__init__()
        self.summary = summary


class TranscriptItem(GObject.Object):
    """A row of the note view: a recording header when ``segment`` is None."""

    def __init__(
        self,
        recording: Recording,
        number: int,
        segment: Segment | None = None,
        speaker_name: str | None = None,
        color_slot: int | None = None,
        preview: bool = False,
        live_summary: str | None = None,
        position: int | None = None,
    ) -> None:
        super().__init__()
        self.recording = recording
        self.number = number
        """1-based position of the recording in its note."""
        self.segment = segment
        self.speaker_name = speaker_name
        self.color_slot = color_slot
        self.preview = preview
        self.live_summary = live_summary
        self.position = position
        """Index of ``segment`` in its recording; None for headers and live rows."""
        self.hidden = False
        """A spare row kept for the next note; it shows nothing and takes no room."""

    @property
    def is_live(self) -> bool:
        """Streamed or preview text, which has no saved segment to edit."""
        return self.position is None or self.preview or self.recording.recording_id == "live"

    def content_key(self) -> tuple[object, ...]:
        """What the row shows; equal keys can keep the same row."""
        return (self.segment, self.preview, self.live_summary, self.speaker_name, self.color_slot)

    def assign(self, other: TranscriptItem) -> None:
        """Show ``other``'s row in this item, so the list keeps its row widgets."""
        self.recording = other.recording
        self.number = other.number
        self.segment = other.segment
        self.speaker_name = other.speaker_name
        self.color_slot = other.color_slot
        self.preview = other.preview
        self.live_summary = other.live_summary
        self.position = other.position
        self.hidden = other.hidden
