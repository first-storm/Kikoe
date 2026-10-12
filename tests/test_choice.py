from dataclasses import replace

from kikoe.core.choice import available_models, resolve_choice
from kikoe.core.models import ApiKind, Capabilities, ModelSpec, Provider

WHISPER = ModelSpec("whisper-1", "Whisper", Capabilities(timestamps=True))
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
