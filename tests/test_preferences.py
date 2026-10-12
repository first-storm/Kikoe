"""Defaults choose stable model identities and accept explicit language hints."""

import pytest

from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Provider
from kikoe.core.preferences import Preferences, preferred_model, valid_language

PROVIDERS = (
    Provider(
        "p",
        "Cloud",
        ApiKind.DEEPGRAM,
        "https://example.com",
        (ModelSpec("a", "A", Capabilities()), ModelSpec("b", "B", Capabilities())),
    ),
)


@pytest.mark.parametrize("language", ["zh", "en", "ja", "zh-CN", "pt-BR", "zh-Hant-TW"])
def test_common_language_tags_are_accepted(language: str) -> None:
    assert valid_language(language)


@pytest.mark.parametrize("language", ["", "中文", "en_US", "en--US", "en/US", " en", "a"])
def test_malformed_language_tags_are_rejected(language: str) -> None:
    assert not valid_language(language)


@pytest.mark.parametrize(
    "recent,default,expected",
    [
        (("p", "b"), ("p", "a"), "b"),
        (("gone", "b"), ("p", "b"), "b"),
        (None, ("p", "b"), "b"),
        (("p", "gone"), ("p", "gone"), "a"),
        (None, None, "a"),
    ],
)
def test_model_preference_falls_back_in_order(
    recent: tuple[str, str] | None, default: tuple[str, str] | None, expected: str
) -> None:
    choice = preferred_model(PROVIDERS, Preferences(default, recent))
    assert choice is not None and choice.model.model_id == expected


def test_no_configured_models_means_no_selection() -> None:
    assert preferred_model((), Preferences()) is None
