"""Render a note as text or subtitles. Display options never change the stored transcript."""

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from kikoe.core.formatting import format_timestamp_range
from kikoe.core.models import DisplayOptions, Note, Segment
from kikoe.core.speakers import resolved_note, speaker_numbers


class ExportFormat(StrEnum):
    TEXT = "txt"
    MARKDOWN = "md"
    SRT = "srt"
    WEBVTT = "vtt"

    @property
    def is_subtitle(self) -> bool:
        """Subtitle formats always include timestamps."""
        return self in {ExportFormat.SRT, ExportFormat.WEBVTT}


_FALLBACK_CUE_MS = 2000
"""Length of a synthesized cue when a segment has no timestamps (text-only models)."""

_FALLBACK_SHIFT_MS = 1000
"""Minimum cue length after clamping, so rounding collisions stay valid."""

_RESERVED_FILENAME_CHARS = frozenset('<>:"/\\|?*')
"""Characters invalid in Windows file names; replaced when deriving save names."""


@dataclass(frozen=True, slots=True)
class _Cue:
    number: int
    start_ms: int
    end_ms: int
    speaker: str | None
    text: str


def export_note(note: Note, fmt: ExportFormat, options: DisplayOptions) -> str:
    """Return ``note`` rendered in ``fmt``.

    Prose formats honor ``options``; subtitle formats always include timestamps
    and only follow the speaker setting. The result ends with a single newline,
    except SRT with no cues, which is empty.
    """
    note = resolved_note(note)
    match fmt:
        case ExportFormat.TEXT:
            return _render_prose(note, options, markdown=False)
        case ExportFormat.MARKDOWN:
            return _render_prose(note, options, markdown=True)
        case ExportFormat.SRT:
            return _render_srt(note, options)
        case ExportFormat.WEBVTT:
            return _render_vtt(note, options)


def export_filename(stem: str, extension: str) -> str:
    """A safe save-dialog name from ``stem`` (e.g. a note title) plus ``extension``."""
    collapsed = " ".join(stem.split())
    cleaned = (
        "".join(
            "-" if char in _RESERVED_FILENAME_CHARS or ord(char) < 0x20 or char == "\x7f" else char
            for char in collapsed
        )
        .strip(".")
        .strip()
    )
    return f"{cleaned or 'Untitled Note'}.{extension}"


def _speaker_labels(note: Note) -> dict[str, str]:
    """Display name per speaker id, numbered note-wide like the note view."""
    return {
        speaker_id: note.speaker_name(speaker_id) or f"Speaker {number}"
        for speaker_id, number in speaker_numbers(note).items()
    }


def _render_prose(note: Note, options: DisplayOptions, *, markdown: bool) -> str:
    labels = _speaker_labels(note)
    blocks = [f"# {note.title}" if markdown else note.title]
    multi = len(note.recordings) > 1
    for number, recording in enumerate(note.recordings, start=1):
        lines = [
            _prose_line(segment, labels, options, markdown=markdown)
            for segment in recording.segments
            if segment.text.strip()
        ]
        if not multi:
            if lines:
                blocks.append("\n".join(lines))
            continue
        heading = f"## Recording {number}" if markdown else f"Recording {number}"
        blocks.append(f"{heading}\n\n{'\n'.join(lines)}" if lines else heading)
    return "\n\n".join(blocks) + "\n"


def _prose_line(
    segment: Segment, labels: dict[str, str], options: DisplayOptions, *, markdown: bool
) -> str:
    prefix = ""
    if options.show_timestamps:
        span = format_timestamp_range(segment.start, segment.end)
        if span:
            prefix += f"[{span}] "
    name = labels.get(segment.speaker_id) if segment.speaker_id is not None else None
    if options.show_speakers and name is not None:
        prefix += f"**{name}:** " if markdown else f"{name}: "
    return f"{prefix}{segment.text.strip()}"


def _render_srt(note: Note, options: DisplayOptions) -> str:
    cues = _subtitle_cues(note)
    if not cues:
        return ""
    blocks: list[str] = []
    for cue in cues:
        text = f"{cue.speaker}: {cue.text}" if options.show_speakers and cue.speaker else cue.text
        start = _format_subtitle_ms(cue.start_ms, ",")
        end = _format_subtitle_ms(cue.end_ms, ",")
        blocks.append(f"{cue.number}\n{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"


def _render_vtt(note: Note, options: DisplayOptions) -> str:
    blocks = ["WEBVTT"]
    for cue in _subtitle_cues(note):
        if options.show_speakers and cue.speaker:
            text = f"<v {_escape_vtt(cue.speaker)}>{_escape_vtt(cue.text)}</v>"
        else:
            text = _escape_vtt(cue.text)
        start = _format_subtitle_ms(cue.start_ms, ".")
        end = _format_subtitle_ms(cue.end_ms, ".")
        blocks.append(f"{start} --> {end}\n{text}")
    return "\n\n".join(blocks) + "\n"


def _subtitle_cues(note: Note) -> tuple[_Cue, ...]:
    """One continuous cue track: recordings are offset by their durations."""
    labels = _speaker_labels(note)
    cues: list[_Cue] = []
    offset_ms = 0
    for recording in note.recordings:
        segments = [s for s in recording.segments if s.text.strip()]
        cursor_ms = 0
        for index, segment in enumerate(segments):
            start_ms = _cue_start_ms(segment, cursor_ms)
            end_ms = _cue_end_ms(segments, index, start_ms)
            speaker = labels.get(segment.speaker_id) if segment.speaker_id is not None else None
            cues.append(
                _Cue(
                    len(cues) + 1,
                    offset_ms + start_ms,
                    offset_ms + end_ms,
                    speaker,
                    _cue_text(segment.text),
                )
            )
            cursor_ms = max(cursor_ms, end_ms)
        offset_ms += _to_ms(recording.duration)
    return tuple(cues)


def _cue_text(text: str) -> str:
    """Drop blank lines: in SRT and WebVTT a blank line ends the cue and loses the rest."""
    return "\n".join(line for raw in text.splitlines() if (line := raw.strip()))


def _cue_start_ms(segment: Segment, cursor_ms: int) -> int:
    if segment.start is None:
        return cursor_ms
    return max(0, _to_ms(segment.start))


def _cue_end_ms(segments: list[Segment], index: int, start_ms: int) -> int:
    """The segment's end, else the next timed start if later, else a fallback slot."""
    end = segments[index].end
    if end is not None:
        end_ms = max(0, _to_ms(end))
    else:
        end_ms = start_ms + _FALLBACK_CUE_MS
        if index + 1 < len(segments):
            following = segments[index + 1].start
            if following is not None and (candidate := max(0, _to_ms(following))) > start_ms:
                end_ms = candidate
    return end_ms if end_ms > start_ms else start_ms + _FALLBACK_SHIFT_MS


def _to_ms(value: timedelta) -> int:
    """Whole milliseconds, rounded; integer math so binary floats cannot shift a cue."""
    return (value + timedelta(microseconds=500)) // timedelta(milliseconds=1)


def _format_subtitle_ms(total_ms: int, separator: str) -> str:
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    seconds, millis = divmod(rest, 1_000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{millis:03}"


def _escape_vtt(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
