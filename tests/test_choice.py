from dataclasses import replace

from helpers import file_caps
from kikoe.core.choice import (
    RequestedFeatures,
    available_models,
    normalize_granularities,
    resolve_choice,
    unsupported_features,
)
from kikoe.core.models import (
    ApiKind,
    Capabilities,
    ModeCapabilities,
    ModelSpec,
    Provider,
    TimestampGranularity,
)
from kikoe.core.validation import Capability

WHISPER = ModelSpec("whisper-1", "Whisper", file_caps(timestamps=True))
GPT = ModelSpec("gpt-4o-transcribe", "GPT-4o", Capabilities())
OPENAI = Provider("o", "OpenAI", ApiKind.OPENAI, "https://api.openai.com/v1", (WHISPER, GPT))
EMPTY = Provider("e", "Empty", ApiKind.OPENAI_COMPATIBLE, "http://localhost/v1")
GROQ = Provider(
    "g", "Groq", ApiKind.OPENAI_COMPATIBLE, "https://api.groq.com/openai/v1", (WHISPER,)
)


def test_available_models_lists_every_model_in_order() -> None:
    assert [c.key for c in available_models([OPENAI, EMPTY, GROQ])] == [
        ("o", "whisper-1"),
        ("o", "gpt-4o-transcribe"),
        ("g", "whisper-1"),
    ]


def test_wanted_model_is_kept_and_refreshed() -> None:
    renamed = replace(GROQ, name="Groq Cloud")

    choice = resolve_choice([OPENAI, renamed], ("g", "whisper-1"))

    assert choice is not None
    assert choice.provider.name == "Groq Cloud"


def test_removed_model_falls_back_to_the_first() -> None:
    choice = resolve_choice([EMPTY, GROQ], ("o", "gpt-4o-transcribe"))

    assert choice is not None
    assert choice.key == ("g", "whisper-1")


def test_no_models_means_no_choice() -> None:
    assert resolve_choice([EMPTY], None) is None


def test_requested_features_are_limited_to_the_model() -> None:
    wanted = RequestedFeatures(timestamps=True, diarization=False)

    assert wanted.allowed_by(ModeCapabilities(timestamps=True, diarization=True)) == wanted
    assert wanted.allowed_by(ModeCapabilities()) == RequestedFeatures(False, False)


def test_granularity_defaults_to_segment() -> None:
    assert RequestedFeatures().timestamp_granularities == (TimestampGranularity.SEGMENT,)


def test_allowed_by_keeps_granularity_only_with_word_timestamps() -> None:
    wanted = RequestedFeatures(
        timestamp_granularities=(TimestampGranularity.WORD, TimestampGranularity.SEGMENT)
    )

    words = ModeCapabilities(timestamps=True, word_timestamps=True)
    assert wanted.allowed_by(words).timestamp_granularities == (
        TimestampGranularity.SEGMENT,
        TimestampGranularity.WORD,
    )
    assert wanted.allowed_by(ModeCapabilities()).timestamp_granularities == (
        TimestampGranularity.SEGMENT,
    )


def test_normalize_granularities_dedupes_orders_and_falls_back() -> None:
    assert normalize_granularities(
        [
            TimestampGranularity.WORD,
            TimestampGranularity.WORD,
            TimestampGranularity.SEGMENT,
        ]
    ) == (TimestampGranularity.SEGMENT, TimestampGranularity.WORD)
    assert normalize_granularities([]) == (TimestampGranularity.SEGMENT,)


def test_unsupported_features_feed_the_hint() -> None:
    assert unsupported_features(ModeCapabilities(timestamps=True)) == (Capability.DIARIZATION,)
    assert unsupported_features(ModeCapabilities(timestamps=True, diarization=True)) == ()
