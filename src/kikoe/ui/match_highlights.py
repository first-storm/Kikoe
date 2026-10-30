"""Find matches tinted in the transcript rows, including rows that get a widget later."""

from collections.abc import Sequence

from kikoe.core.find_replace import SegmentKey, TextMatch
from kikoe.core.models import Note
from kikoe.ui.items import TranscriptItem
from kikoe.ui.stale_rows import StaleRows
from kikoe.ui.transcript_row import TranscriptRow


class MatchHighlights:
    """Remembers the matches of each segment and tints them whenever its row is bound."""

    def __init__(self, bound: dict[TranscriptRow, TranscriptItem], stale: StaleRows) -> None:
        self._bound = bound
        self._stale = stale
        self._found: dict[SegmentKey, tuple[str, list[tuple[int, int]]]] = {}
        """Per segment, the text searched and where in it the matches are."""
        self._current: TextMatch | None = None

    def show(
        self, note: Note | None, matches: Sequence[TextMatch], current: TextMatch | None
    ) -> None:
        """Tint ``matches``, found in ``note``, in the rows on screen and those bound later."""
        recordings = {r.recording_id: r for r in note.recordings} if note else {}
        found: dict[SegmentKey, tuple[str, list[tuple[int, int]]]] = {}
        for match in matches:
            recording = recordings.get(match.recording_id)
            if recording is None:
                continue
            text = recording.segments[match.position].text
            found.setdefault(match.key, (text, []))[1].append((match.start, match.end))
        self._found = found
        self._current = current
        for row, item in self._bound.items():
            if row not in self._stale:
                self.apply(row, item)

    def clear(self) -> None:
        self.show(None, (), None)

    def apply(self, row: TranscriptRow, item: TranscriptItem) -> None:
        """Tint the matches in ``row``, which was just bound to ``item``."""
        segment = item.segment
        if segment is None or item.position is None or item.is_live or item.hidden:
            return
        key = item.recording.recording_id, item.position
        text, ranges = self._found.get(key, (segment.text, []))
        if text != segment.text:
            # Found before the segment changed; a refresh with its new text is on its way.
            text, ranges = segment.text, []
        current = self._current
        span = (current.start, current.end) if current is not None and current.key == key else None
        row.text_view.show_matches(text, ranges, span)
