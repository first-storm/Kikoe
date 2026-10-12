"""GObject wrappers so core dataclasses can live in a ``Gio.ListStore``."""

from gi.repository import GObject

from kikoe.core.choice import ModelChoice
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
    ) -> None:
        super().__init__()
        self.recording = recording
        self.number = number
        """1-based position of the recording in its note."""
        self.segment = segment
        self.speaker_name = speaker_name


class ModelChoiceItem(GObject.Object):
    def __init__(self, choice: ModelChoice) -> None:
        super().__init__()
        self.choice = choice
