"""Translate SDK failures without leaking authentication URLs or headers."""

from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError


def sdk_error(error: Exception) -> TranscriptionError:
    if isinstance(error, TranscriptionError):
        return error
    if isinstance(error, ValueError | TypeError | KeyError | AttributeError):
        return TranscriptionError(_("The provider sent a response Kikoe does not understand."))
    status: object = getattr(error, "status_code", getattr(error, "code", None))
    response: object = getattr(error, "response", None)
    if status is None and response is not None:
        status = getattr(response, "status_code", None)
    if status in (401, 403):
        return TranscriptionError(_("The provider rejected the API key. Check it in Preferences."))
    if isinstance(status, int) and 400 <= status < 500 and status != 429:
        return TranscriptionError(
            _(
                "The provider rejected this request (HTTP {status}). Check the model and options."
            ).format(status=status)
        )
    return TranscriptionError(
        _("Could not complete transcription. Check your connection and try again."), retryable=True
    )
