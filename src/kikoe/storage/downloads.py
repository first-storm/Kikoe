"""Fetch pinned files over HTTPS; a file only appears once its SHA-256 matches.

Downloads stream into ``<name>.part`` next to the destination and are renamed into
place after verification, so a cancelled, failed or tampered download leaves nothing
behind that could later be mistaken for a good file.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO
from urllib.parse import urlsplit

from kikoe.core.local_models import Download
from kikoe.i18n import _

if TYPE_CHECKING:
    import httpx

log = logging.getLogger(__name__)

type ProgressCallback = Callable[[int], None]
"""Called with the number of bytes received since the previous call."""

_FLUSH_BYTES = 1 << 20
"""Write in 1 MiB batches so disk writes do not run on the main thread one chunk at a time."""


class DownloadError(Exception):
    """A download failed; the message is shown to the user."""


async def download(
    item: Download,
    destination: Path,
    on_progress: ProgressCallback,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Download ``item`` to ``destination``, replacing nothing until it is verified."""
    import httpx

    if urlsplit(item.url).scheme != "https":
        raise DownloadError(_("Downloads must use a secure connection."))
    partial = destination.with_name(destination.name + ".part")
    try:
        async with (
            httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(30, read=120),
                transport=transport,
            ) as client,
            client.stream("GET", item.url) as response,
        ):
            _check_response(response)
            digest = await _receive(response, partial, item.size, on_progress)
        if digest != item.sha256:
            log.warning("Checksum mismatch for %s", item.filename)
            raise DownloadError(
                _("“{name}” did not match its expected checksum.").format(name=item.filename)
            )
        await asyncio.to_thread(partial.replace, destination)
    except httpx.HTTPError as error:
        log.warning("Download of %s failed: %s", item.filename, type(error).__name__)
        raise DownloadError(
            _("Could not download “{name}”. Check your internet connection.").format(
                name=item.filename
            )
        ) from error
    finally:
        await asyncio.to_thread(partial.unlink, missing_ok=True)


def _check_response(response: httpx.Response) -> None:
    if response.url.scheme != "https":
        raise DownloadError(_("Downloads must use a secure connection."))
    if response.status_code != 200:
        log.warning("Download returned HTTP %d", response.status_code)
        raise DownloadError(
            _("The download server answered with error {status}.").format(
                status=response.status_code
            )
        )


async def _receive(
    response: httpx.Response, partial: Path, expected: int, on_progress: ProgressCallback
) -> str:
    """Stream the body to ``partial`` and return its SHA-256; never more than ``expected``."""
    digest = hashlib.sha256()
    received = 0
    pending = bytearray()
    handle: BinaryIO = await asyncio.to_thread(partial.open, "wb")
    try:
        async for chunk in response.aiter_bytes():
            received += len(chunk)
            if received > expected:
                raise DownloadError(_("The downloaded file is larger than expected."))
            digest.update(chunk)
            pending += chunk
            on_progress(len(chunk))
            if len(pending) >= _FLUSH_BYTES:
                await asyncio.to_thread(handle.write, bytes(pending))
                pending.clear()
        await asyncio.to_thread(handle.write, bytes(pending))
    finally:
        await asyncio.to_thread(handle.close)
    if received != expected:
        raise DownloadError(_("The download ended early. Try again."))
    return digest.hexdigest()
