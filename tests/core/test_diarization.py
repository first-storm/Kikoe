"""Matching recognised speech to speaker turns found by a separate model."""

from datetime import timedelta

from kikoe.core.diarization import SpeakerTurn, TimedWord, assign_speakers, speaker_at
from kikoe.core.models import Segment


def s(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


TURNS = (
    SpeakerTurn(s(0), s(3), "1"),
    SpeakerTurn(s(3), s(6), "2"),
    SpeakerTurn(s(8), s(10), "1"),
)


def test_the_speaker_with_the_most_overlap_wins() -> None:
    assert speaker_at(s(2), s(5), TURNS) == "2"
    assert speaker_at(s(2.5), s(3.2), TURNS) == "1"


def test_speech_in_a_gap_joins_a_turn_close_by_but_not_a_distant_one() -> None:
    assert speaker_at(s(6.2), s(6.5), TURNS) == "2"
    assert speaker_at(s(7.6), s(7.9), TURNS) == "1"
    assert speaker_at(s(20), s(21), TURNS) is None


def test_words_are_regrouped_into_turns_when_speakers_alternate() -> None:
    words = (
        TimedWord("Hello", s(0.5), s(1)),
        TimedWord("there.", s(1), s(2)),
        TimedWord("Hi", s(3.5), s(4)),
        TimedWord("back", s(4), s(5)),
        TimedWord("again", s(8.5), s(9)),
    )
    merged = assign_speakers((Segment("ignored", s(0), s(9)),), words, TURNS)
    assert merged == (
        Segment("Hello there.", s(0.5), s(2), "1"),
        Segment("Hi back", s(3.5), s(5), "2"),
        Segment("again", s(8.5), s(9), "1"),
    )


def test_chinese_words_are_joined_without_spaces() -> None:
    words = (TimedWord("你好", s(0), s(1)), TimedWord("世界", s(1), s(2)))
    (segment,) = assign_speakers((), words, TURNS)
    assert segment.text == "你好世界"


def test_without_words_whole_segments_get_a_speaker_and_untimed_ones_none() -> None:
    segments = (Segment("a", s(0), s(2)), Segment("b", s(3.5), s(5)), Segment("c"))
    merged = assign_speakers(segments, (), TURNS)
    assert [m.speaker_id for m in merged] == ["1", "2", None]


def test_no_turns_leaves_the_transcript_unchanged() -> None:
    segments = (Segment("a", s(0), s(2)),)
    assert assign_speakers(segments, (TimedWord("a", s(0), s(2)),), ()) == segments
