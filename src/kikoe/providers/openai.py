"""Client for OpenAI's audio transcription endpoint, which Groq and many others also offer."""

import asyncio
import logging
import mimetypes
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx

from kikoe.core.models import ModelSpec, Segment
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError, TranscriptionOptions

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25_000_000
"""OpenAI and Groq (free tier) both reject uploads above 25 MB."""

_TIMEOUT = httpx.Timeout(30.0, write=300.0, read=900.0)
"""Long audio is transcribed while the request waits, so reading may take minutes."""


class OpenAIClient:
    """Uploads a file to ``{base_url}/audio/transcriptions``.

    Asks for segment timestamps (``verbose_json``) only when the model supports them,
    because newer models such as ``gpt-4o-transcribe`` reject that format.
    """

    max_upload_bytes: int | None = MAX_UPLOAD_BYTES

    def __init__(
        self, base_url: str, api_key: str, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"
        self._host = urlsplit(base_url).hostname or base_url
        self._api_key = api_key
        self._transport = transport

    async def transcribe_file(
        self, audio: Path, model: ModelSpec, options: TranscriptionOptions
    ) -> tuple[Segment, ...]:
        size = (await asyncio.to_thread(audio.stat)).st_size
        if size > MAX_UPLOAD_BYTES:
            raise TranscriptionError(_("The audio file is too large to upload to this provider."))
        content = await asyncio.to_thread(audio.read_bytes)
        timed = model.capabilities.timestamps and options.timestamps
        data: dict[str, str] = {"model": model.model_id}
        if timed:
            data |= {"response_format": "verbose_json", "timestamp_granularities[]": "segment"}
        else:
            data["response_format"] = "json"
        if options.language:
            data["language"] = options.language
        mime = mimetypes.guess_type(audio.name)[0] or "application/octet-stream"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}

        log.info("Uploading %s (%d bytes) to %s", audio.name, len(content), self._url)
        response = await self._post(data, (audio.name, content, mime), headers)
        if response.is_error:
            raise self._status_error(response)
        try:
            body: object = response.json()
            return _parse(body, timed)
        except (ValueError, TypeError, KeyError) as error:
            log.warning("Unexpected response from %s: %s", self._host, error)
            raise TranscriptionError(
                _("{host} sent a response Kikoe does not understand.").format(host=self._host)
            ) from error

    async def _post(
        self, data: dict[str, str], file: tuple[str, bytes, str], headers: dict[str, str]
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT) as client:
                return await client.post(
                    self._url, data=data, files={"file": file}, headers=headers
                )
        except httpx.TimeoutException as error:
            raise TranscriptionError(
                _("{host} took too long to respond. Try again later.").format(host=self._host)
            ) from error
        except httpx.HTTPError as error:
            raise TranscriptionError(
                _("Could not reach {host}: {error}").format(host=self._host, error=error)
            ) from error

    def _status_error(self, response: httpx.Response) -> TranscriptionError:
        log.warning("%s answered %d: %s", self._host, response.status_code, response.text[:500])
        if response.status_code == HTTPStatus.UNAUTHORIZED:
            return TranscriptionError(
                _("{host} rejected the API key. Check it in Preferences.").format(host=self._host)
            )
        if response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE:
            return TranscriptionError(_("The audio file is too large to upload to this provider."))
        message = _error_message(response)
        if message:
            return TranscriptionError(
                _("{host} reported an error: {message}").format(host=self._host, message=message)
            )
        return TranscriptionError(
            _("{host} answered with HTTP status {status}.").format(
                host=self._host, status=response.status_code
            )
        )


def _parse(body: object, timed: bool) -> tuple[Segment, ...]:
    fields = _as_dict(body)
    raw_segments = fields.get("segments")
    if timed and isinstance(raw_segments, list) and raw_segments:
        segments = (_parse_segment(entry) for entry in cast(list[object], raw_segments))
        return tuple(segment for segment in segments if segment.text)
    text = str(fields["text"]).strip()
    return (Segment(text),) if text else ()


def _parse_segment(entry: object) -> Segment:
    fields = _as_dict(entry)
    return Segment(
        text=str(fields["text"]).strip(),
        start=timedelta(seconds=_as_float(fields["start"])),
        end=timedelta(seconds=_as_float(fields["end"])),
    )


def _error_message(response: httpx.Response) -> str | None:
    """The ``error.message`` that OpenAI-style APIs put in failed responses, if present."""
    try:
        body: object = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = cast(dict[str, object], body).get("error")
    if isinstance(error, dict):
        message = cast(dict[str, object], error).get("message")
        return str(message) if message else None
    return str(error) if isinstance(error, str) else None


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")
    return cast(dict[str, object], value)


def _as_float(value: object) -> float:
    if not isinstance(value, int | float):
        raise TypeError("expected a number")
    return float(value)
