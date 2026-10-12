"""Validate SDK payloads and retain only metadata requested by the user."""

import math
from collections.abc import Iterable
from datetime import timedelta
from typing import cast

from kikoe.core.models import Segment


def fields(value: object) -> dict[str, object]:
    """Validate the boundary with loosely typed SDK payloads."""
    if not isinstance(value, dict):
        raise TypeError("Expected an object")
    return cast(dict[str, object], value)


def entries(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return cast(list[object], value)


def seconds(value: object) -> timedelta | None:
    """Normalize numeric seconds and Google's protobuf duration strings."""
    if value is None:
        return None
    if isinstance(value, str) and value.endswith("s"):
        value = float(value[:-1])
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise TypeError("Expected seconds")
    if value < 0 or not math.isfinite(value):
        raise ValueError("Invalid timestamp")
    return timedelta(seconds=value)


def parse_segments(body: object, timed: bool, diarize: bool) -> tuple[Segment, ...]:
    """Preserve sentence segments when the endpoint supplies them."""
    data = fields(body)
    result: list[Segment] = []
    for item in entries(data.get("segments")):
        part = fields(item)
        text_value = part["text"]
        if not isinstance(text_value, str):
            raise TypeError("Expected transcript text")
        text = text_value.strip()
        speaker = part.get("speaker")
        if text:
            if timed and (part.get("start") is None or part.get("end") is None):
                raise ValueError("Missing segment timestamps")
            result.append(
                Segment(
                    text,
                    seconds(part["start"]) if timed else None,
                    seconds(part["end"]) if timed else None,
                    str(speaker) if diarize and speaker is not None else None,
                )
            )
    if result:
        return tuple(result)
    if words := entries(data.get("words")):
        return parse_words(words, timed, diarize)
    text = data.get("text")
    if not isinstance(text, str):
        raise TypeError("Missing transcript text")
    return (Segment(text.strip()),) if text.strip() else ()


def parse_words(words: Iterable[object], timed: bool, diarize: bool) -> tuple[Segment, ...]:
    """Normalize SDK word spelling before pure domain grouping."""
    from kikoe.core.transcript_words import TranscriptWord, group_words

    normalized: list[TranscriptWord] = []
    for item in words:
        word = fields(item)
        text = str(
            word.get("punctuated_word") or word.get("word") or word.get("text") or ""
        ).strip()
        if not text:
            continue
        speaker = word.get("speaker_label") or word.get("speaker")
        normalized.append(
            TranscriptWord(
                text,
                seconds(word.get("start", word.get("start_offset"))) if timed else None,
                seconds(word.get("end", word.get("end_offset"))) if timed else None,
                str(speaker) if diarize and speaker is not None else None,
            )
        )
    return group_words(tuple(normalized))
