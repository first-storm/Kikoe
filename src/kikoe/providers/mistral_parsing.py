"""Normalize Mistral transcription payloads, whether complete or from a stream's last event."""

from kikoe.core.models import Segment
from kikoe.providers.parsing import entries, fields, parse_segments


def parse_mistral(body: object, timed: bool, diarize: bool) -> tuple[Segment, ...]:
    """Map Mistral's ``speaker_id`` to transcript speaker labels."""
    data = fields(body)
    items = [
        {**fields(item), "speaker": fields(item).get("speaker_id")}
        for item in entries(data.get("segments"))
    ]
    return parse_segments({**data, "segments": items}, timed, diarize)
