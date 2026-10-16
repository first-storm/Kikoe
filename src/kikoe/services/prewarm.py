"""Prepare SDKs and credentials in the background before the first transcription."""

import asyncio
import logging
import time
from collections.abc import Callable

from kikoe.core.models import Provider
from kikoe.providers.base import TranscriptionProvider
from kikoe.providers.factory import create_client
from kikoe.services.file_transcription import ApiKeys

log = logging.getLogger(__name__)


async def prewarm_api_key(provider: Provider, keys: ApiKeys) -> None:
    """Pay the credential store's first-read cost without retaining the key."""
    started = time.perf_counter()
    try:
        await keys.get_api_key(provider.provider_id)
    except Exception:
        log.warning("Could not prepare credentials for provider %s", provider.provider_id)
        return
    log.debug(
        "Prepared credentials for provider %s in %.0f ms",
        provider.provider_id,
        (time.perf_counter() - started) * 1000,
    )


async def prewarm_provider(
    provider: Provider,
    create: Callable[[Provider, str], TranscriptionProvider] | None = None,
) -> None:
    """Import the SDK behind ``provider``; failure only costs the first request some time.

    Building a client does no network I/O, and importing happens in a worker thread so the
    window stays responsive. Nothing is raised: this is an optimisation, not a feature.
    """
    started = time.perf_counter()
    try:
        await asyncio.to_thread(create or create_client, provider, "")
    except Exception:
        log.warning("Could not prepare provider %s ahead of time", provider.provider_id)
        log.debug("Prewarm failure details", exc_info=True)
        return
    log.debug(
        "Prepared provider %s in %.0f ms",
        provider.provider_id,
        (time.perf_counter() - started) * 1000,
    )
