"""Shared test utilities."""

import asyncio
import functools
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Protocol

from kikoe.core.models import Capabilities, ModeCapabilities
from kikoe.storage.model_catalog import load_catalog
from kikoe.storage.settings import SettingsStore
from kikoe.storage.settings_writer import SettingsWriter


def run_async[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    """Run an async test body to completion; avoids a pytest plugin for a handful of tests."""

    @functools.wraps(test)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return wrapper


def file_caps(
    *,
    timestamps: bool = False,
    diarization: bool = False,
    streaming: bool = False,
) -> Capabilities:
    """A model that only uploads files, as most tests need."""
    return Capabilities(
        file=ModeCapabilities(
            timestamps=timestamps,
            diarization=diarization,
            streaming=streaming,
        )
    )


LIVE_ONLY = Capabilities(file=None, realtime=ModeCapabilities())
"""A model that can only stream while recording."""


def open_settings(path: Path) -> SettingsWriter:
    """The shared settings writer for a settings file, as the application builds it."""
    return SettingsWriter(SettingsStore(path), load_catalog())


class SentRequest(Protocol):
    """A recorded HTTP request; the SDKs use ``httpx`` or ``httpx2``, which share this shape."""

    @property
    def content(self) -> bytes: ...


def form_field(request: SentRequest, name: str) -> str | None:
    """Read a plain field from the multipart body (good enough for these small requests)."""
    marker = f'name="{name}"\r\n\r\n'.encode()
    body = request.content
    if marker not in body:
        return None
    start = body.index(marker) + len(marker)
    return body[start : body.index(b"\r\n", start)].decode()
