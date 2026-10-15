"""Turning a note, or a transcription in progress, into the rows of the note view."""

from collections.abc import Callable, Sequence
from itertools import islice

from gi.repository import Gio, GLib, Gtk

from kikoe.core.formatting import SPEAKER_COLOR_COUNT
from kikoe.core.models import Note, Recording
from kikoe.core.speakers import identity_for, live_speaker_numbers, speaker_numbers
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.i18n import _
from kikoe.ui.items import TranscriptItem

_FOLLOW_DISTANCE = 48
"""How close to the end the view must be scrolled to keep following new text."""


def build_rows(note: Note) -> list[TranscriptItem]:
    """A header row per recording, each followed by its segments."""
    numbers = speaker_numbers(note)
    rows: list[TranscriptItem] = []
    for number, recording in enumerate(note.recordings, start=1):
        rows.append(TranscriptItem(recording, number))
        for position, segment in enumerate(recording.segments):
            speaker = segment.speaker_id
            if speaker is None:
                rows.append(TranscriptItem(recording, number, segment, position=position))
                continue
            identity = identity_for(recording, speaker)
            speaker_number = numbers[identity]
            name = note.speaker_name(identity) or _("Speaker {number}").format(
                number=speaker_number
            )
            slot = (speaker_number - 1) % SPEAKER_COLOR_COUNT
            rows.append(TranscriptItem(recording, number, segment, name, slot, position=position))
    return rows


class SegmentIndex:
    """Finds a saved segment's row from where its recording's header row is.

    Edits, Undo and Redo address a segment by recording and position; this keeps
    finding its row independent of the transcript's length.
    """

    def __init__(self, items: Gio.ListStore[TranscriptItem]) -> None:
        self._items = items
        self._headers: dict[str, int] = {}

    def reset(self, rows: Sequence[TranscriptItem]) -> None:
        """Remember the header positions of ``rows``, which now fill the list from the top."""
        self._headers = {
            row.recording.recording_id: index
            for index, row in enumerate(rows)
            if row.segment is None
        }

    def find(self, recording_id: str, position: int) -> int | None:
        """The list index of the segment's row, or None when no row shows it there.

        A transcription overlay may have moved the rows since the last reset; checking
        the row found keeps that from ever pointing at another segment.
        """
        header = self._headers.get(recording_id)
        if header is None:
            return None
        index = header + 1 + position
        item = self._items.get_item(index)
        if (
            item is None
            or item.hidden
            or item.position != position
            or item.recording.recording_id != recording_id
        ):
            return None
        return index


class StreamRenderer:
    """Writes a transcript that is still arriving into the list, keeping unchanged rows."""

    def __init__(
        self,
        items: Gio.ListStore[TranscriptItem],
        scroll: Gtk.ScrolledWindow,
        active: Callable[[], bool],
    ) -> None:
        self._items = items
        self._scroll = scroll
        self._active = active

    def render(
        self,
        snapshot: FileTranscriptSnapshot,
        recording: Recording,
        number: int,
        summary: str,
        previous_rows: list[TranscriptItem],
        base: int,
        context: Note,
    ) -> list[TranscriptItem]:
        """Replace the rows after ``base`` and return the new ones for the next update.

        Rows up to the first one that differs keep their items, so an update costs what
        changed rather than the length of the transcript.
        """
        segments = (*snapshot.segments, *snapshot.preview)
        numbers = live_speaker_numbers(context, segments)
        header = TranscriptItem(recording, number, live_summary=summary)
        kept = 0
        if previous_rows and previous_rows[0].content_key() == header.content_key():
            kept = 1
            for row, segment in zip(
                islice(previous_rows, 1, None), snapshot.segments, strict=False
            ):
                if row.preview or row.segment != segment:
                    break
                kept += 1
        rows = previous_rows[:kept] or [header]
        for index in range(max(kept - 1, 0), len(segments)):
            segment = segments[index]
            speaker = segment.speaker_id
            name = (
                context.speaker_name(speaker)
                or _("Speaker {number}").format(number=numbers[speaker])
                if speaker
                else None
            )
            color = (numbers[speaker] - 1) % SPEAKER_COLOR_COUNT if speaker else None
            preview = index >= len(snapshot.segments)
            rows.append(TranscriptItem(recording, number, segment, name, color, preview=preview))
        adjustment = self._scroll.get_vadjustment()
        follow = (
            adjustment.get_upper() - adjustment.get_value() - adjustment.get_page_size()
            < _FOLLOW_DISTANCE
        )
        self._items.splice(base + kept, len(previous_rows) - kept, rows[kept:])
        if follow:
            GLib.idle_add(self._follow)
        return rows

    def _follow(self) -> bool:
        if self._active():
            adjustment = self._scroll.get_vadjustment()
            adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return False
