"""Defaults accept explicit language hints."""

import pytest

from kikoe.core.preferences import valid_language


@pytest.mark.parametrize("language", ["zh", "en", "ja", "zh-CN", "pt-BR", "zh-Hant-TW"])
def test_common_language_tags_are_accepted(language: str) -> None:
    assert valid_language(language)


@pytest.mark.parametrize("language", ["", "中文", "en_US", "en--US", "en/US", " en", "a"])
def test_malformed_language_tags_are_rejected(language: str) -> None:
    assert not valid_language(language)
