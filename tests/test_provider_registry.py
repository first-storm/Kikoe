import pytest

from kikoe.core.models import ApiKind, AudioFormat, UploadPolicy
from kikoe.core.provider_registry import (
    PROVIDERS,
    UploadHint,
    descriptor,
    is_default_base_url,
)
from kikoe.core.validation import effective_base_url


def test_every_api_kind_has_a_descriptor_in_enum_order() -> None:
    assert tuple(PROVIDERS) == tuple(ApiKind)
    assert tuple(d.api for d in PROVIDERS.values()) == tuple(ApiKind)


def test_display_names_are_unique() -> None:
    names = [d.display_name for d in PROVIDERS.values()]
    assert len(names) == len(set(names))


@pytest.mark.parametrize(
    ("api", "url"),
    [
        (ApiKind.DEEPGRAM, "https://api.deepgram.com/v1"),
        (ApiKind.OPENAI, "https://api.openai.com/v1"),
        (ApiKind.OPENAI_COMPATIBLE, ""),
        (ApiKind.OPENROUTER, "https://openrouter.ai/api/v1"),
        (ApiKind.GEMINI, "https://generativelanguage.googleapis.com"),
        (ApiKind.MIMO, "https://api.xiaomimimo.com/v1"),
        (ApiKind.GROQ, "https://api.groq.com"),
        (ApiKind.MISTRAL, "https://api.mistral.ai"),
        (ApiKind.AUDIO_LLM, ""),
    ],
)
def test_default_base_urls_are_unchanged(api: ApiKind, url: str) -> None:
    assert descriptor(api).default_base_url == url
    assert effective_base_url(api, "  ") == url


@pytest.mark.parametrize(
    ("api", "policy"),
    [
        (ApiKind.DEEPGRAM, UploadPolicy(2_000_000_000)),
        (ApiKind.GEMINI, UploadPolicy(2_000_000_000, 3600)),
        (
            ApiKind.MIMO,
            UploadPolicy(7_500_000, format=AudioFormat.WAV, formats=(AudioFormat.WAV,)),
        ),
        (ApiKind.OPENAI, UploadPolicy(25_000_000, format=AudioFormat.WAV)),
        (ApiKind.MISTRAL, UploadPolicy(500_000_000, 3600)),
        (ApiKind.OPENROUTER, UploadPolicy(25_000_000)),
        (ApiKind.OPENAI_COMPATIBLE, UploadPolicy(25_000_000)),
        (ApiKind.GROQ, UploadPolicy(25_000_000)),
        (
            ApiKind.AUDIO_LLM,
            UploadPolicy(14_000_000, 600, format=AudioFormat.WAV, formats=(AudioFormat.WAV,)),
        ),
    ],
)
def test_upload_policies_are_unchanged(api: ApiKind, policy: UploadPolicy) -> None:
    assert descriptor(api).upload == policy


def test_only_protocols_are_generic_names() -> None:
    generic = [d.api for d in PROVIDERS.values() if d.generic]
    assert generic == [ApiKind.OPENAI_COMPATIBLE, ApiKind.AUDIO_LLM]


def test_only_audio_llm_is_prompted() -> None:
    assert [d.api for d in PROVIDERS.values() if d.prompted] == [ApiKind.AUDIO_LLM]


def test_audio_llm_inline_base64_fits_gemini_request_limit() -> None:
    assert descriptor(ApiKind.AUDIO_LLM).upload.bytes * 4 / 3 < 20_000_000


def test_audio_llm_format_endpoints_count_as_defaults() -> None:
    assert is_default_base_url("https://generativelanguage.googleapis.com")
    assert not is_default_base_url("https://llm.example.test/v1")


def test_upload_hints_mark_the_apis_with_special_limits() -> None:
    hinted = {d.api: d.upload_hints for d in PROVIDERS.values() if d.upload_hints}
    assert hinted == {
        ApiKind.OPENAI_COMPATIBLE: frozenset({UploadHint.SERVER_MAY_LIMIT_SIZE}),
        ApiKind.AUDIO_LLM: frozenset({UploadHint.SERVER_MAY_LIMIT_SIZE}),
        ApiKind.GEMINI: frozenset({UploadHint.DURATION_DEPENDS_ON_OPTIONS}),
    }


def test_default_endpoints_are_recognised_but_custom_ones_are_not() -> None:
    assert is_default_base_url("https://api.groq.com")
    assert not is_default_base_url("https://example.test/v1")
    assert not is_default_base_url("")


def test_sdk_loggers_cover_every_sdk_provider() -> None:
    names = {name for d in PROVIDERS.values() for name in d.sdk_loggers}
    assert names == {"deepgram", "openai", "openrouter", "google.genai", "groq", "mistralai"}
