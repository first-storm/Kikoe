"""Shared test utilities."""

import asyncio
import functools
from collections.abc import Callable, Coroutine

from kikoe.core.models import Capabilities, ModeCapabilities


def run_async[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    """Run an async test body to completion; avoids a pytest plugin for a handful of tests."""

    @functools.wraps(test)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return wrapper


def file_caps(
    *,
    timestamps: bool = False,
    word_timestamps: bool = False,
    diarization: bool = False,
    streaming: bool = False,
) -> Capabilities:
    """A model that only uploads files, as most tests need."""
    return Capabilities(
        file=ModeCapabilities(
            timestamps=timestamps,
            word_timestamps=word_timestamps,
            diarization=diarization,
            streaming=streaming,
        )
    )


LIVE_ONLY = Capabilities(file=None, realtime=ModeCapabilities())
"""A model that can only stream while recording."""
