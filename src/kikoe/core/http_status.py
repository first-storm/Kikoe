"""Group HTTP status codes by what the user can do about them."""

from enum import StrEnum


class StatusKind(StrEnum):
    BAD_REQUEST = "bad-request"
    UNAUTHORIZED = "unauthorized"
    PAYMENT_REQUIRED = "payment-required"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not-found"
    TIMEOUT = "timeout"
    TOO_LARGE = "too-large"
    UNPROCESSABLE = "unprocessable"
    RATE_LIMITED = "rate-limited"
    SERVER_ERROR = "server-error"
    OTHER = "other"


def classify_status(status: int) -> StatusKind:
    """Map a status code to a kind; the providers document these codes consistently."""
    match status:
        case 400:
            return StatusKind.BAD_REQUEST
        case 401:
            return StatusKind.UNAUTHORIZED
        case 402:
            return StatusKind.PAYMENT_REQUIRED
        case 403:
            return StatusKind.FORBIDDEN
        case 404:
            return StatusKind.NOT_FOUND
        case 408 | 504:
            return StatusKind.TIMEOUT
        case 413:
            return StatusKind.TOO_LARGE
        case 415 | 422:
            return StatusKind.UNPROCESSABLE
        case 429:
            return StatusKind.RATE_LIMITED
        case _ if 500 <= status < 600:
            return StatusKind.SERVER_ERROR
        case _:
            return StatusKind.OTHER


def is_retryable(kind: StatusKind) -> bool:
    """Waiting helps for these; credentials, credit and request problems need the user."""
    return kind in (StatusKind.TIMEOUT, StatusKind.RATE_LIMITED, StatusKind.SERVER_ERROR)
