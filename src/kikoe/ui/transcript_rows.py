"""Turning a note, or a transcription in progress, into the rows of the note view."""

from collections.abc import Callable

from gi.repository import Gio, GLib, Gtk

from kikoe.core.formatting import SPEAKER_COLOR_COUNT
from kikoe.core.models import Note, Recording
from kikoe.core.speakers import identity_for, live_speaker_numbers, speaker_numbers
from kikoe.core.transcript_rows import shared_prefix
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
        """Replace the rows after ``base`` and return the new ones for the next update."""
        segments = (*snapshot.segments, *snapshot.preview)
        numbers = live_speaker_numbers(context, segments)
        rows = [TranscriptItem(recording, number, live_summary=summary)]
        for index, segment in enumerate(segments):
            speaker = segment.speaker_id
            name = (
                context.speaker_name(speaker)
                or _("Speaker {number}").format(number=numbers[speaker])
                if speaker
                else None
            )
            color = (numbers[speaker] - 1) % SPEAKER_COLOR_COUNT if speaker else None
            preview = index >= len(snapshot.segments)
            new = TranscriptItem(recording, number, segment, name, color, preview=preview)
            old = previous_rows[index + 1] if index + 1 < len(previous_rows) else None
            rows.append(old if old is not None and old.content_key() == new.content_key() else new)
        adjustment = self._scroll.get_vadjustment()
        follow = (
            adjustment.get_upper() - adjustment.get_value() - adjustment.get_page_size()
            < _FOLLOW_DISTANCE
        )
        prefix = shared_prefix(
            [row.content_key() for row in previous_rows], [row.content_key() for row in rows]
        )
        self._items.splice(base + prefix, len(previous_rows) - prefix, rows[prefix:])
        if follow:
            GLib.idle_add(self._follow)
        return rows

    def _follow(self) -> bool:
        if self._active():
            adjustment = self._scroll.get_vadjustment()
            adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        return False
