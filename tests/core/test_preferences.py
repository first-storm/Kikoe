"""Defaults accept explicit language hints."""

import pytest

from kikoe.core.models import ApiKind, Provider
from kikoe.core.preferences import Preferences, recent_provider, valid_language


@pytest.mark.parametrize("language", ["zh", "zh-CN", "zh-Hant-TW"])
def test_common_language_tags_are_accepted(language: str) -> None:
    assert valid_language(language)


@pytest.mark.parametrize("language", ["", "中文", "en_US", "en--US", "en/US", " en", "a"])
def test_malformed_language_tags_are_rejected(language: str) -> None:
    assert not valid_language(language)


def _provider(provider_id: str) -> Provider:
    return Provider(provider_id, provider_id, ApiKind.GROQ, "https://example.test/v1")


def test_recent_provider_uses_the_recent_model() -> None:
    providers = [_provider("a"), _provider("b")]
    assert recent_provider(Preferences(recent_model=("a", "m")), providers) == providers[0]


def test_recent_provider_is_none_without_a_usable_preference() -> None:
    assert recent_provider(Preferences(), [_provider("a")]) is None
    assert recent_provider(Preferences(recent_model=("gone", "m")), [_provider("a")]) is None
