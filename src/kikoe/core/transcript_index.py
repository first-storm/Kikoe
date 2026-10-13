"""Index transcript rows without copying segments or constructing UI objects."""

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass

from kikoe.core.models import Note


@dataclass(frozen=True, slots=True)
class TranscriptIndex:
    """Recording offsets and display metadata shared by lazily created rows."""

    offsets: tuple[int, ...]
    count: int
    identities: tuple[Mapping[str, str], ...]
    speaker_order: Mapping[str, int]
    has_speakers: bool
    has_timestamps: bool

    @classmethod
    def build(cls, note: Note) -> TranscriptIndex:
        """Inspect metadata once, without resolving copies of every segment."""
        offsets: list[int] = []
        identities: list[Mapping[str, str]] = []
        order: dict[str, int] = {}
        count = 0
        timestamps = False
        for recording in note.recordings:
            offsets.append(count)
            count += 1 + len(recording.segments)
            bindings = {b.source_id: b.speaker_id for b in recording.speaker_bindings}
            identities.append(bindings)
            for segment in recording.segments:
                if segment.speaker_id is not None:
                    person = bindings.get(segment.speaker_id, segment.speaker_id)
                    order.setdefault(person, len(order))
                timestamps |= segment.start is not None or segment.end is not None
        return cls(tuple(offsets), count, tuple(identities), order, bool(order), timestamps)

    def locate(self, position: int) -> tuple[int, int | None]:
        """Return the recording index and segment position; None identifies its header."""
        if not 0 <= position < self.count:
            raise IndexError(position)
        recording = bisect_right(self.offsets, position) - 1
        segment = position - self.offsets[recording] - 1
        return recording, segment if segment >= 0 else None
