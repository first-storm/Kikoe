"""API limits and live revisions are domain rules, independent of widgets."""

from datetime import timedelta

import pytest

from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Segment
from kikoe.core.transcript_words import TranscriptWord, group_words
from kikoe.core.transcription import (
    LiveTranscript,
    SpeechBoundary,
    TranscriptEvent,
    model_support,
    scope_speakers,
)


@pytest.mark.parametrize(
    ("api", "id", "file", "realtime", "timestamps", "speakers"),
    [
        (ApiKind.OPENAI, "gpt-live-transcribe", False, True, False, False),
        (ApiKind.OPENAI, "gpt-4o-transcribe", True, True, False, False),
        (ApiKind.OPENAI, "whisper-1", True, False, True, False),
        (ApiKind.OPENAI, "gpt-4o-transcribe-diarize", True, False, True, True),
        (ApiKind.OPENROUTER, "openai/whisper-1", True, False, True, True),
        (ApiKind.GEMINI, "gemini-3.5-transcribe-live", False, True, False, False),
        (ApiKind.GEMINI, "gemini-3.5-transcribe", True, False, True, True),
        (ApiKind.DEEPGRAM, "nova-3", True, True, True, True),
    ],
)
def test_known_api_limits_override_manual_capability_claims(
    api: ApiKind, id: str, file: bool, realtime: bool, timestamps: bool, speakers: bool
) -> None:
    support = model_support(api, ModelSpec(id, id, Capabilities(True, True, True)))
    assert support.file is file
    assert support.recording == Capabilities(realtime, timestamps, speakers)


def test_final_replaces_preview_and_duplicate_final_without_appending() -> None:
    text = LiveTranscript()
    text.apply(TranscriptEvent("1", (Segment("hel"),), False))
    text.apply(TranscriptEvent("1", (Segment("hello"),), False))
    assert text.segments == ()
    assert text.preview == (Segment("hello"),)
    text.apply(TranscriptEvent("1", (Segment("Hello."),), True))
    text.apply(TranscriptEvent("1", (Segment("Hello."),), True))
    text.apply(TranscriptEvent("1", (Segment("stale"),), False))
    assert text.segments == (Segment("Hello."),)
    assert text.preview == ()


def test_speaker_zero_is_scoped_without_merging_independent_recordings() -> None:
    source = (Segment("hello", speaker_id="0"), Segment("text"))
    first = scope_speakers(source, "recording-1")
    second = scope_speakers(source, "recording-2")
    assert first[0].speaker_id != second[0].speaker_id
    assert first[1].speaker_id is None


def test_word_grouping_preserves_speaker_changes_and_chinese_spacing() -> None:
    words = tuple(
        TranscriptWord(t, timedelta(seconds=i), timedelta(seconds=i + 1), s)
        for i, (t, s) in enumerate([("你", "0"), ("好。", "0"), ("Hello", "1"), ("there.", "1")])
    )
    assert group_words(words) == (
        Segment("你好。", timedelta(), timedelta(seconds=2), "0"),
        Segment("Hello there.", timedelta(seconds=2), timedelta(seconds=4), "1"),
    )


def test_manual_commit_requires_speech_and_flushes_after_half_second_pause() -> None:
    boundary = SpeechBoundary()
    assert not boundary.feed(timedelta(seconds=10), 0)
    assert not boundary.feed(timedelta(milliseconds=100), 1)
    assert not boundary.feed(timedelta(milliseconds=400), 0)
    assert boundary.feed(timedelta(milliseconds=100), 0)
    assert not boundary.feed(timedelta(seconds=10), 0)


def test_manual_commit_limits_unbroken_speech_to_fifteen_seconds() -> None:
    boundary = SpeechBoundary()
    assert not boundary.feed(timedelta(seconds=14), 1)
    assert boundary.feed(timedelta(seconds=1), 1)
