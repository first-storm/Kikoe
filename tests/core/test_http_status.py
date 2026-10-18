"""Status codes group by what the user can do about them."""

import pytest

from kikoe.core.http_status import StatusKind, classify_status, is_retryable


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, StatusKind.BAD_REQUEST),
        (401, StatusKind.UNAUTHORIZED),
        (402, StatusKind.PAYMENT_REQUIRED),
        (403, StatusKind.FORBIDDEN),
        (404, StatusKind.NOT_FOUND),
        (408, StatusKind.TIMEOUT),
        (504, StatusKind.TIMEOUT),
        (413, StatusKind.TOO_LARGE),
        (415, StatusKind.UNPROCESSABLE),
        (422, StatusKind.UNPROCESSABLE),
        (429, StatusKind.RATE_LIMITED),
        (500, StatusKind.SERVER_ERROR),
        (503, StatusKind.SERVER_ERROR),
        (409, StatusKind.OTHER),
        (499, StatusKind.OTHER),
    ],
)
def test_status_codes_are_classified_by_documented_meaning(status: int, kind: StatusKind) -> None:
    assert classify_status(status) is kind


def test_only_transient_kinds_are_retryable() -> None:
    retryable = {k for k in StatusKind if is_retryable(k)}

    assert retryable == {StatusKind.TIMEOUT, StatusKind.RATE_LIMITED, StatusKind.SERVER_ERROR}
