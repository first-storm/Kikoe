"""Keep recognition labels separate from the people a note describes."""

from dataclasses import replace
from datetime import timedelta

from kikoe.core.models import Note, Recording, Segment, Speaker, SpeakerBinding


def resolved_segments(recording: Recording) -> tuple[Segment, ...]:
    """Resolve local labels without destroying the information needed to re-match them."""
    identities = {binding.source_id: binding.speaker_id for binding in recording.speaker_bindings}
    return tuple(
        replace(
            segment,
            speaker_id=(
                identities.get(segment.speaker_id, segment.speaker_id)
                if segment.speaker_id is not None
                else None
            ),
        )
        for segment in recording.segments
    )


def resolved_note(note: Note) -> Note:
    """Provide the same identities to every display and export consumer."""
    return replace(
        note,
        recordings=tuple(
            replace(recording, segments=resolved_segments(recording), speaker_bindings=())
            for recording in note.recordings
        ),
    )


def sample_segment(recording: Recording, source_id: str) -> Segment | None:
    """Choose a quoted turn even when a model cannot supply playable timestamps."""
    candidates = [s for s in recording.segments if s.speaker_id == source_id and s.text.strip()]
    return next(
        (s for s in candidates if sample_range(recording, s) is not None),
        candidates[0] if candidates else None,
    )


def sample_range(recording: Recording, segment: Segment) -> tuple[timedelta, timedelta] | None:
    """Bound a sample to its turn and ten seconds, never guessing its start."""
    if segment.start is None or segment.start < timedelta():
        return None
    end = segment.end
    if end is None:
        end = min(
            (
                s.start
                for s in recording.segments
                if s.start is not None and s.start > segment.start
            ),
            default=recording.duration,
        )
    end = min(end, recording.duration, segment.start + timedelta(seconds=10))
    return (segment.start, end) if end > segment.start else None


def preserved_identities(old: Recording, new: tuple[Segment, ...]) -> dict[str, str]:
    """Only inherit a person when timed evidence is strong and unambiguous."""
    previous = resolved_segments(old)
    old_ranges = _ranges(previous)
    new_ranges = _ranges(new)
    result: dict[str, str] = {}
    for source_id, intervals in new_ranges.items():
        if any(
            s.speaker_id == source_id
            and (s.start is None or s.end is None or s.end <= s.start or s.start < timedelta())
            for s in new
        ):
            continue
        total = sum((end - start for start, end in intervals), timedelta())
        scores = sorted(
            (
                (
                    sum(
                        (
                            max(timedelta(), min(end, old_end) - max(start, old_start))
                            for start, end in intervals
                            for old_start, old_end in ranges
                        ),
                        timedelta(),
                    ),
                    person,
                )
                for person, ranges in old_ranges.items()
            ),
            reverse=True,
        )
        if not scores or total <= timedelta():
            continue
        best, person = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else timedelta()
        if best >= total * 0.8 and best > runner_up and best >= runner_up * 2:
            result[source_id] = person
    return result


def _ranges(segments: tuple[Segment, ...]) -> dict[str, list[tuple[timedelta, timedelta]]]:
    grouped: dict[str, list[tuple[timedelta, timedelta]]] = {}
    for segment in segments:
        if (
            segment.speaker_id is not None
            and segment.start is not None
            and segment.end is not None
            and segment.end > segment.start >= timedelta()
        ):
            grouped.setdefault(segment.speaker_id, []).append((segment.start, segment.end))
    for identity, intervals in grouped.items():
        merged: list[tuple[timedelta, timedelta]] = []
        for start, end in sorted(intervals):
            if merged and start <= merged[-1][1]:
                merged[-1] = merged[-1][0], max(end, merged[-1][1])
            else:
                merged.append((start, end))
        grouped[identity] = merged
    return grouped


def unresolved_recordings(note: Note) -> tuple[Recording, ...]:
    """Recordings requiring a deliberate choice, including keeping a new person."""
    return tuple(r for r in note.recordings if any(not b.confirmed for b in r.speaker_bindings))


def identity_for(recording: Recording, source_id: str) -> str:
    """Resolve a recognition label while retaining its identity for edit actions."""
    return next(
        (b.speaker_id for b in recording.speaker_bindings if b.source_id == source_id), source_id
    )


def matching_candidates(note: Note) -> tuple[Speaker, ...]:
    """Keep established people and named old identities available after re-transcription."""
    established = {
        binding.speaker_id
        for recording in note.recordings
        for binding in recording.speaker_bindings
        if binding.confirmed
    }
    return tuple(s for s in note.speakers if s.name or s.speaker_id in established)


def speaker_numbers(note: Note) -> dict[str, int]:
    """The “Speaker N” number per identity: its stable ordinal, else its first appearance.

    Legacy speakers have ordinal zero, so they are numbered by where they first speak in the
    resolved transcript, counting every speaker from one.
    """
    appearing = dict.fromkeys(
        s.speaker_id
        for r in note.recordings
        for s in resolved_segments(r)
        if s.speaker_id is not None
    )
    first = {speaker_id: index for index, speaker_id in enumerate(appearing, start=1)}
    return first | {s.speaker_id: s.ordinal for s in note.speakers if s.ordinal > 0}


def live_speaker_numbers(note: Note, segments: tuple[Segment, ...]) -> dict[str, int]:
    """Allocate preview numbers as persistence will, including retained old identities."""
    numbers = {s.speaker_id: s.ordinal for s in note.speakers if s.ordinal > 0}
    following = max(numbers.values(), default=0) + 1
    for segment in segments:
        if segment.speaker_id is not None and segment.speaker_id not in numbers:
            numbers[segment.speaker_id] = following
            following += 1
    return numbers


def binding_choices(
    recording: Recording, choices: dict[str, str | None]
) -> tuple[SpeakerBinding, ...]:
    """Keeping a new speaker restores the original independent identity."""
    return tuple(
        replace(b, speaker_id=choices[b.source_id] or b.original_id, confirmed=True)
        if b.source_id in choices
        else b
        for b in recording.speaker_bindings
    )
