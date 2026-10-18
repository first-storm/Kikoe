"""SDK failures become messages that match the HTTP status."""

import pytest

from kikoe.providers.errors import body_detail, redact, sdk_error


class StatusError(Exception):
    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class GeminiLikeError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__("boom")
        self.code = code


@pytest.mark.parametrize(
    ("status", "text", "retryable"),
    [
        (401, "rejected the API key", False),
        (402, "run out of credits", False),
        (403, "does not allow this API key", False),
        (404, "could not find this model", False),
        (408, "took too long", True),
        (413, "too large", False),
        (422, "could not process this audio", False),
        (429, "limiting requests", True),
        (502, "having problems (HTTP 502)", True),
    ],
)
def test_status_selects_matching_message(status: int, text: str, retryable: bool) -> None:
    error = sdk_error(StatusError(status))

    assert text in str(error)
    assert error.retryable is retryable
    assert "model and options" not in str(error)


def test_bad_request_keeps_model_hint_and_service_detail() -> None:
    error = sdk_error(StatusError(400, "corrupt audio"))

    assert "Check the model and options" in str(error)
    assert "corrupt audio" in str(error)


def test_code_attribute_is_used_when_there_is_no_status_code() -> None:
    assert "limiting requests" in str(sdk_error(GeminiLikeError(429)))


def test_long_service_detail_is_truncated() -> None:
    error = sdk_error(StatusError(400, "x" * 1000))

    assert len(str(error)) < 400


def test_failure_without_status_is_a_retryable_connection_problem() -> None:
    error = sdk_error(ConnectionError("down"))

    assert "connection" in str(error)
    assert error.retryable


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"error": {"message": " nested "}, "message": "top"}, "nested"),
        ({"error": {"message": ""}, "err_msg": "MiMo style"}, "MiMo style"),
        ({"error": "not an object", "message": "top"}, "top"),
        ({"message": "  "}, None),
        ("raw text", None),
    ],
)
def test_body_detail_prefers_the_nested_message(body: object, expected: str | None) -> None:
    assert body_detail(body) == expected


def test_redact_hides_the_key_and_leaves_text_alone_without_one() -> None:
    assert redact("bad key sk-1", "sk-1") == "bad key [redacted]"
    assert redact("no key here", "") == "no key here"
