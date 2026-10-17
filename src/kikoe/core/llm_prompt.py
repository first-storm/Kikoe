"""Prompts and output cleanup for transcribing with a general audio-capable LLM."""

import re

from kikoe.core.models import LlmFormat, LlmSettings

DEFAULT_PROMPT = """\
Transcribe the speech in this audio verbatim.

- Write it in the language that is spoken; never translate it.
- Do not summarize, answer, explain or comment on what is said.
- Use normal punctuation and start a new paragraph when the topic or speaker changes.
- Output only the transcript, without any introduction, Markdown, labels or timestamps.
- If there is no speech, output nothing."""
"""Sent to the model, not shown as UI text, so it is not translated."""

FORMAT_BASE_URLS: dict[LlmFormat, str] = {
    LlmFormat.OPENAI: "",
    LlmFormat.GEMINI: "https://generativelanguage.googleapis.com",
}
"""Endpoint offered when the request format is chosen; OpenAI-compatible servers vary."""

_FENCE = re.compile(r"\A```[^\n]*\n(?P<body>.*?)\n?```\Z", re.DOTALL)


def effective_prompt(settings: LlmSettings) -> str:
    """The user's prompt, or the built-in one when they have not written their own."""
    return settings.prompt.strip() or DEFAULT_PROMPT


def render_prompt(settings: LlmSettings, language: str | None) -> str:
    """The text sent with each audio chunk; a chosen language becomes a hint."""
    prompt = effective_prompt(settings)
    if not language:
        return prompt
    return f"{prompt}\n\nLanguage hint: the speech is in {language} (BCP 47)."


def stored_prompt(text: str) -> str:
    """What to save for an edited prompt: unchanged default text is stored as empty."""
    return "" if text.strip() == DEFAULT_PROMPT else text.strip()


def clean_transcript(text: str) -> str:
    """Models sometimes wrap their whole answer in a code block despite the prompt."""
    text = text.strip()
    match = _FENCE.match(text)
    return match["body"].strip() if match else text
