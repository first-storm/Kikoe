"""Finding and replacing text in the open note's transcript.

Matches never span segments: each segment is saved and edited on its own, so a match
across a boundary could be neither shown in one text view nor replaced in one edit.
"""

import re
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from kikoe.core.models import Note

type SegmentKey = tuple[str, int]
"""A saved segment's recording ID and position, the way edits address it."""


@dataclass(frozen=True, slots=True)
class FindQuery:
    """What the find entry holds, with the search options."""

    text: str
    match_case: bool = False
    whole_words: bool = False
    regex: bool = False


class InvalidPatternError(ValueError):
    """The query is not a valid regular expression."""


class InvalidReplacementError(ValueError):
    """The replacement refers to a group the pattern does not have, or has a bad escape."""


@dataclass(frozen=True, slots=True)
class TextMatch:
    """Where text was found: character offsets into one segment's text."""

    recording_id: str
    position: int
    start: int
    end: int

    @property
    def key(self) -> SegmentKey:
        return self.recording_id, self.position


@dataclass(frozen=True, slots=True)
class Replacement:
    """The outcome of replacing matches, before it is applied to the note."""

    texts: Mapping[SegmentKey, str]
    """New text of each segment that changed."""
    replaced: int
    """Matches replaced, including in segments whose text came out the same."""
    kept: int
    """Segments left as they were because they would have been empty."""
    inserted: tuple[TextMatch, ...]
    """Where the replacement text is in the new texts, to search on after it."""


def compile_query(query: FindQuery) -> re.Pattern[str] | None:
    """The pattern for ``query``, or None when there is nothing to find."""
    if not query.text:
        return None
    pattern = query.text if query.regex else re.escape(query.text)
    flags = re.MULTILINE if query.match_case else re.MULTILINE | re.IGNORECASE
    try:
        compiled = re.compile(pattern, flags)
        if query.whole_words:
            # Not \b, which would never match a word that ends in punctuation, like C++.
            # Wrapping is compiled after the bare pattern, as it could balance a stray
            # parenthesis and turn an invalid expression into a different valid one.
            compiled = re.compile(rf"(?<!\w)(?:{pattern})(?!\w)", flags)
    except re.error as error:
        raise InvalidPatternError(str(error)) from error
    return compiled


def find_matches(note: Note, pattern: re.Pattern[str]) -> tuple[TextMatch, ...]:
    """Every match in the note's saved segments, in reading order.

    Empty matches (e.g. of ``x*``) are skipped: there is nothing to show or replace.
    """
    return tuple(
        TextMatch(recording.recording_id, position, found.start(), found.end())
        for recording in note.recordings
        for position, segment in enumerate(recording.segments)
        for found in pattern.finditer(segment.text)
        if found.end() > found.start()
    )


def first_match_from(
    note: Note, matches: Sequence[TextMatch], recording_id: str, position: int, offset: int
) -> int | None:
    """Index of the first match at or after a place in the note, wrapping to the first.

    None when there are no matches. A place in a recording the note no longer has
    counts as the start of the note.
    """
    if not matches:
        return None
    order = {r.recording_id: index for index, r in enumerate(note.recordings)}

    def place(match: TextMatch) -> tuple[int, int, int]:
        return order.get(match.recording_id, -1), match.position, match.start

    index = bisect_left(matches, (order.get(recording_id, -1), position, offset), key=place)
    return index if index < len(matches) else 0


def replace_matches(
    note: Note,
    pattern: re.Pattern[str],
    matches: Sequence[TextMatch],
    replacement: str,
    *,
    regex: bool,
) -> Replacement:
    """Replace ``matches``, as ``find_matches`` found them with ``pattern`` in ``note``.

    Segments are saved without surrounding whitespace and are never empty, so new text
    is stripped and a segment it would leave empty keeps its text. With ``regex``,
    ``\\1`` and ``\\g<name>`` in ``replacement`` insert the matched groups; otherwise it
    is inserted as written. Matches no longer in the note are skipped.
    """
    wanted: dict[SegmentKey, set[tuple[int, int]]] = {}
    for match in matches:
        wanted.setdefault(match.key, set()).add((match.start, match.end))
    texts: dict[SegmentKey, str] = {}
    inserted: list[TextMatch] = []
    replaced = kept = 0
    for key, spans in wanted.items():
        recording = note.recording(key[0])
        if recording is None or not 0 <= key[1] < len(recording.segments):
            continue
        text = recording.segments[key[1]].text
        raw, places = _replace_in(text, pattern, spans, replacement, regex=regex)
        if not places:
            continue
        result = raw.strip()
        if not result:
            kept += 1
            continue
        replaced += len(places)
        if result != text:
            texts[key] = result
        lead = len(raw) - len(raw.lstrip())
        for start, end in places:
            inserted.append(
                TextMatch(*key, _clamp(start - lead, result), _clamp(end - lead, result))
            )
    return Replacement(texts, replaced, kept, tuple(inserted))


def _replace_in(
    text: str,
    pattern: re.Pattern[str],
    spans: set[tuple[int, int]],
    replacement: str,
    *,
    regex: bool,
) -> tuple[str, list[tuple[int, int]]]:
    """``text`` with the matches at ``spans`` replaced, and where the new text landed."""
    pieces: list[str] = []
    places: list[tuple[int, int]] = []
    size = last = 0
    for found in pattern.finditer(text):
        if found.span() not in spans:
            continue
        before = text[last : found.start()]
        new = _expand(found, replacement) if regex else replacement
        pieces += (before, new)
        size += len(before)
        places.append((size, size + len(new)))
        size += len(new)
        last = found.end()
    pieces.append(text[last:])
    return "".join(pieces), places


def _expand(found: re.Match[str], replacement: str) -> str:
    try:
        return found.expand(replacement)
    # An unknown group name raises IndexError rather than re.error.
    except (re.error, IndexError) as error:
        raise InvalidReplacementError(str(error)) from error


def _clamp(offset: int, text: str) -> int:
    return min(max(offset, 0), len(text))
