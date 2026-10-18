from dataclasses import replace

import pytest

from kikoe.core.choice import (
    ModelChoice,
    RequestedFeatures,
    available_models,
    language_dropped,
    model_matches,
    resolve_choice,
    unsupported_features,
)
from kikoe.core.models import (
    ApiKind,
    Capabilities,
    ModeCapabilities,
    ModelKey,
    ModelSpec,
    Provider,
)
from kikoe.core.validation import Capability
from kikoe.providers.base import TranscriptionOptions
from tests.helpers import file_caps

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
    assert resolve_choice([], ("o", "whisper-1"), None) is None


@pytest.mark.parametrize(
    ("current", "recent", "expected"),
    [
        (("o", "gpt-4o-transcribe"), ("o", "whisper-1"), "gpt-4o-transcribe"),
        (None, ("o", "gpt-4o-transcribe"), "gpt-4o-transcribe"),
        (("gone", "x"), ("o", "gpt-4o-transcribe"), "gpt-4o-transcribe"),
        (("gone", "x"), ("o", "gone"), "whisper-1"),
        (None, None, "whisper-1"),
    ],
)
def test_current_then_recent_model_are_tried_before_the_first(
    current: ModelKey | None, recent: ModelKey | None, expected: str
) -> None:
    choice = resolve_choice([OPENAI], current, recent)
    assert choice is not None and choice.model.model_id == expected


def _gpt() -> ModelChoice:
    return ModelChoice(OPENAI, GPT)


@pytest.mark.parametrize("query", ["", "   "])
def test_empty_model_search_keeps_every_model(query: str) -> None:
    assert model_matches(_gpt(), query)


def test_model_search_ignores_case_and_surrounding_space() -> None:
    assert model_matches(_gpt(), "  gpt-4O ")


def test_model_search_matches_the_model_id_and_provider_name() -> None:
    assert model_matches(_gpt(), "4o-transcribe")
    assert model_matches(_gpt(), "openai")


def test_model_search_words_may_come_from_different_fields_in_any_order() -> None:
    assert model_matches(_gpt(), "transcribe OpenAI")


def test_model_search_rejects_models_missing_any_word() -> None:
    assert not model_matches(_gpt(), "whisper")
    assert not model_matches(_gpt(), "openai whisper")


def test_model_search_words_do_not_span_two_fields() -> None:
    assert not model_matches(_gpt(), "transcribeopenai")


def test_requested_features_are_limited_to_the_model() -> None:
    wanted = RequestedFeatures(timestamps=True, diarization=False)

    assert wanted.allowed_by(ModeCapabilities(timestamps=True, diarization=True)) == wanted
    assert wanted.allowed_by(ModeCapabilities()) == RequestedFeatures(False, False)


def test_request_options_are_limited_like_the_features_they_extend() -> None:
    options = TranscriptionOptions(True, True, language="zh")

    assert options.allowed_by(ModeCapabilities(diarization=True)) == RequestedFeatures(False, True)


def test_unsupported_features_feed_the_hint() -> None:
    assert unsupported_features(ModeCapabilities(timestamps=True)) == (Capability.DIARIZATION,)
    assert unsupported_features(ModeCapabilities(timestamps=True, diarization=True)) == ()


def test_mistral_drops_the_language_when_timestamps_or_speakers_are_requested() -> None:
    for features in (RequestedFeatures(True, False), RequestedFeatures(False, True)):
        assert language_dropped(ApiKind.MISTRAL, features, "zh")


def test_mistral_keeps_the_language_for_plain_text() -> None:
    assert not language_dropped(ApiKind.MISTRAL, RequestedFeatures(False, False), "zh")


def test_language_is_never_dropped_without_one_or_for_other_services() -> None:
    assert not language_dropped(ApiKind.MISTRAL, RequestedFeatures(), None)
    assert not language_dropped(ApiKind.MISTRAL, RequestedFeatures(), "")
    assert not language_dropped(ApiKind.GROQ, RequestedFeatures(), "zh")
