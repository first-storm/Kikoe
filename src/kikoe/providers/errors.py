"""Translate SDK failures without leaking authentication URLs or headers."""

from typing import cast

import groq
import openai

from kikoe.core.http_status import StatusKind, classify_status, is_retryable
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError

_DETAIL_LIMIT = 200


def status_of(error: BaseException) -> int | None:
    """Read the HTTP status the various SDK exception types carry, if any."""
    status: object = getattr(error, "status_code", getattr(error, "code", None))
    response: object = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _text(value: object) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def redact(text: str, key: str) -> str:
    """Services sometimes echo the API key back in their error text."""
    return text.replace(key, "[redacted]") if key else text


def unreadable(host: str) -> TranscriptionError:
    """The response did not have the shape the SDK or Kikoe expects."""
    return TranscriptionError(
        _("{host} sent a response Kikoe does not understand.").format(host=host)
    )


def body_detail(body: object) -> str | None:
    """The message in a JSON error body; OpenAI nests it under ``error``, others do not."""
    if not isinstance(body, dict):
        return None
    data = cast(dict[str, object], body)
    nested: object = data.get("error")
    if isinstance(nested, dict):
        message = _text(cast(dict[str, object], nested).get("message"))
        if message:
            return message
    return _text(data.get("err_msg")) or _text(data.get("message"))


def status_error(status: int, subject: str, detail: str | None = None) -> TranscriptionError:
    """Explain an HTTP failure; `subject` is the provider or host name shown to the user."""
    kind = classify_status(status)
    retryable = is_retryable(kind)
    match kind:
        case StatusKind.UNAUTHORIZED:
            message = _("{subject} rejected the API key. Check it in Preferences.")
        case StatusKind.FORBIDDEN:
            message = _(
                "{subject} does not allow this API key to use this model, or is not available "
                "in your region. Check your account and the key in Preferences."
            )
        case StatusKind.PAYMENT_REQUIRED:
            message = _("{subject} reports that your account has run out of credits.")
        case StatusKind.NOT_FOUND:
            message = _("{subject} could not find this model. Check the model name.")
        case StatusKind.TIMEOUT:
            message = _("{subject} took too long to respond. Try again later.")
        case StatusKind.TOO_LARGE:
            message = _("The audio is too large for {subject}.")
        case StatusKind.RATE_LIMITED:
            message = _(
                "{subject} is limiting requests, or your quota is used up. "
                "Wait a while and try again, or check your plan."
            )
        case StatusKind.SERVER_ERROR:
            message = _("{subject} is having problems (HTTP {status}). Try again later.")
        case StatusKind.UNPROCESSABLE:
            message = _("{subject} could not process this audio (HTTP {status}).")
        case StatusKind.BAD_REQUEST:
            message = _(
                "{subject} rejected this request (HTTP {status}). Check the model and options."
            )
        case StatusKind.OTHER:
            message = _("{subject} returned an unexpected error (HTTP {status}).")
    text = message.format(subject=subject, status=status)
    if detail and len(detail) > _DETAIL_LIMIT:
        detail = detail[: _DETAIL_LIMIT - 1] + "…"
    if detail and kind in (StatusKind.BAD_REQUEST, StatusKind.UNPROCESSABLE, StatusKind.OTHER):
        text = _("{message} Details: {detail}").format(message=text, detail=detail)
    return TranscriptionError(text, retryable=retryable)


def sdk_error(error: Exception) -> TranscriptionError:
    if isinstance(error, TranscriptionError):
        return error
    if isinstance(error, ValueError | TypeError | KeyError | AttributeError):
        return TranscriptionError(_("The provider sent a response Kikoe does not understand."))
    status = status_of(error)
    if status is not None:
        detail = _text(getattr(error, "message", None)) or body_detail(getattr(error, "body", None))
        return status_error(status, _("The provider"), detail)
    return TranscriptionError(
        _("Could not complete transcription. Check your connection and try again."), retryable=True
    )


def openai_error(error: openai.APIError | groq.APIError, host: str, key: str) -> TranscriptionError:
    """Explain a failure from the OpenAI SDK or Groq's SDK, which shares its error classes.

    Their ``message`` embeds the raw response body, so the body's own message comes first.
    """
    match error:
        case openai.APIStatusError() | groq.APIStatusError():
            message = body_detail(error.body) or error.message
            return status_error(error.status_code, host, redact(message, key))
        case openai.APITimeoutError() | groq.APITimeoutError():
            message = _("{host} took too long to respond. Try again later.")
        case openai.APIConnectionError() | groq.APIConnectionError():
            message = _("Could not reach {host}. Try again later.")
        case _:
            return TranscriptionError(
                _("{host} could not finish transcription: {message}").format(
                    host=host, message=redact(error.message, key)
                ),
                retryable=True,
            )
    return TranscriptionError(message.format(host=host), retryable=True)
