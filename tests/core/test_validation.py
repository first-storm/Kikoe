import pytest

from kikoe.core.models import ApiKind, Capabilities, ModeCapabilities
from kikoe.core.validation import (
    Capability,
    ModelError,
    ProviderError,
    capability_list,
    effective_base_url,
    validate_model,
    validate_provider,
)

GROQ_URL = "https://api.groq.com/openai/v1"


def test_provider_name_must_not_be_blank() -> None:
    error = validate_provider("  ", ApiKind.OPENAI_COMPATIBLE, GROQ_URL)
    assert error is ProviderError.EMPTY_NAME


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "example.com",
        "https://",
        "http:///x",
        "http://[::1",
        "http://[not-an-ip]",
    ],
)
def test_provider_url_must_be_http_with_host(url: str) -> None:
    error = validate_provider("Groq", ApiKind.OPENAI_COMPATIBLE, url)
    assert error is ProviderError.INVALID_BASE_URL


def test_openai_compatible_requires_a_base_url() -> None:
    error = validate_provider("Groq", ApiKind.OPENAI_COMPATIBLE, "")
    assert error is ProviderError.EMPTY_BASE_URL


@pytest.mark.parametrize("api", [ApiKind.DEEPGRAM, ApiKind.OPENAI, ApiKind.MIMO])
def test_hosted_apis_accept_an_empty_base_url(api: ApiKind) -> None:
    assert validate_provider("Hosted", api, "") is None


def test_valid_provider_has_no_error() -> None:
    assert validate_provider("Groq", ApiKind.OPENAI_COMPATIBLE, GROQ_URL) is None


@pytest.mark.parametrize("url", ["http://[::1]:8000", "http://[::1]/v1"])
def test_valid_ipv6_provider_has_no_error(url: str) -> None:
    assert validate_provider("IPv6", ApiKind.OPENAI_COMPATIBLE, url) is None


def test_effective_base_url_uses_default_when_empty() -> None:
    assert effective_base_url(ApiKind.OPENAI, " ") == "https://api.openai.com/v1"


def test_effective_base_url_trims_and_drops_trailing_slash() -> None:
    assert effective_base_url(ApiKind.OPENAI_COMPATIBLE, f" {GROQ_URL}/ ") == GROQ_URL


def test_model_id_must_not_be_blank() -> None:
    assert validate_model(" ", []) is ModelError.EMPTY_MODEL_ID


def test_model_id_must_be_unique_within_provider() -> None:
    assert validate_model("nova-3", ["nova-3"]) is ModelError.DUPLICATE_MODEL_ID


def test_editing_a_model_may_keep_its_own_id() -> None:
    assert validate_model("nova-3", ["nova-3", "nova-2"], editing_id="nova-3") is None


def test_editing_a_model_cannot_take_another_models_id() -> None:
    error = validate_model("nova-2", ["nova-3", "nova-2"], editing_id="nova-3")
    assert error is ModelError.DUPLICATE_MODEL_ID


def test_capability_list_is_empty_for_text_only_models() -> None:
    assert capability_list(Capabilities()) == ()


def test_capability_list_keeps_display_order() -> None:
    caps = Capabilities(
        file=ModeCapabilities(timestamps=True, streaming=True),
        realtime=ModeCapabilities(diarization=True),
    )
    assert capability_list(caps) == (
        Capability.REALTIME,
        Capability.TIMESTAMPS,
        Capability.DIARIZATION,
        Capability.STREAMING,
    )


def test_a_model_needs_file_or_realtime_transcription() -> None:
    none = Capabilities(file=None, realtime=None)
    assert validate_model("m", [], capabilities=none) is ModelError.NO_MODE
    assert validate_model("m", [], capabilities=Capabilities()) is None
