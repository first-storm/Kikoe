import pytest

from kikoe.core.llm_prompt import (
    DEFAULT_PROMPT,
    clean_transcript,
    effective_prompt,
    render_prompt,
    stored_prompt,
)
from kikoe.core.models import LlmSettings


def test_empty_prompt_uses_the_built_in_one() -> None:
    assert effective_prompt(LlmSettings()) == DEFAULT_PROMPT
    assert effective_prompt(LlmSettings(prompt="  \n ")) == DEFAULT_PROMPT


def test_custom_prompt_is_sent_as_written() -> None:
    assert render_prompt(LlmSettings(prompt=" Transcribe. "), None) == "Transcribe."


def test_chosen_language_is_appended_as_a_hint() -> None:
    rendered = render_prompt(LlmSettings(prompt="Transcribe."), "zh-CN")
    assert rendered.startswith("Transcribe.\n\n")
    assert "zh-CN" in rendered


def test_unchanged_default_is_stored_as_empty_so_later_defaults_apply() -> None:
    assert stored_prompt(f"{DEFAULT_PROMPT}\n") == ""
    assert stored_prompt(" Mine \n") == "Mine"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  Hello.  ", "Hello."),
        ("```\nHello.\n```", "Hello."),
        ("```text\n你好。\n第二段\n```", "你好。\n第二段"),
        ("Use ``` in code.", "Use ``` in code."),
        ("", ""),
    ],
)
def test_clean_transcript_strips_a_wrapping_code_fence(text: str, expected: str) -> None:
    assert clean_transcript(text) == expected
