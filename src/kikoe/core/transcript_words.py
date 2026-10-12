"""Turn provider words into readable speaker turns without changing their punctuation."""

from dataclasses import dataclass
from datetime import timedelta

from kikoe.core.models import Segment


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """A validated token with optional timing and speaker identity."""

    text: str
    start: timedelta | None
    end: timedelta | None
    speaker: str | None


def group_words(words: tuple[TranscriptWord, ...]) -> tuple[Segment, ...]:
    result: list[Segment] = []
    group: list[TranscriptWord] = []
    for word in words:
        if group and (
            group[-1].speaker != word.speaker
            or group[-1].text.endswith((".", "?", "!", "。", "\uff1f", "\uff01"))
        ):
            result.append(_segment(group))
            group = []
        group.append(word)
    if group:
        result.append(_segment(group))
    return tuple(result)


def _segment(words: list[TranscriptWord]) -> Segment:
    text = words[0].text
    for word in words[1:]:
        # CJK providers commonly emit individual characters, not space-separated words.
        join = "" if _cjk(text[-1]) and _cjk(word.text[0]) else " "
        text += join + word.text
    return Segment(text, words[0].start, words[-1].end, words[0].speaker)


def _cjk(char: str) -> bool:
    return "\u2e80" <= char <= "\u9fff" or "\u3000" <= char <= "\u303f"
