"""Normalize Mistral transcription payloads, whether complete or from a stream's last event."""

from kikoe.core.models import Segment
from kikoe.providers.parsing import entries, fields, parse_segments, parse_words


def parse_mistral(
    body: object, timed: bool, diarize: bool, words_only: bool
) -> tuple[Segment, ...]:
    """Map Mistral's ``speaker_id``; word-level requests return one entry per word."""
    data = fields(body)
    items = [
        {**fields(item), "speaker": fields(item).get("speaker_id")}
        for item in entries(data.get("segments"))
    ]
    if words_only and items:
        return parse_words(items, timed, False)
    return parse_segments({**data, "segments": items}, timed, diarize)
