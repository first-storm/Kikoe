"""Microphone capture. The backend is not chosen yet (see docs/architecture.md)."""

from collections.abc import AsyncIterator
from typing import Protocol


class Recorder(Protocol):
    """Captures mono 16-bit PCM from the default input device."""

    sample_rate: int

    def chunks(self) -> AsyncIterator[bytes]:
        """Yield audio while recording; ends after :meth:`stop`."""
        ...

    async def stop(self) -> None: ...
