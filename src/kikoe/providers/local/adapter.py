"""What an engine package provides to the worker process.

An engine's ``adapter.py`` exposes ``create(engine_dir: Path) -> EngineAdapter``. The
adapter is the only code that imports the engine's own bindings, and it runs only in
the worker, never in the application process.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from kikoe.providers.local.protocol import ErrorKind, Op

type Emit = Callable[[dict[str, object]], None]
"""Sends an event for the request being handled."""


class EngineError(Exception):
    """A request failed in a way the app can explain; ``kind`` picks the user message."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class EngineAdapter(Protocol):
    def handle(self, op: Op, args: dict[str, object], emit: Emit) -> dict[str, object]:
        """Run one request on the worker's compute thread; raise ``EngineError``."""
        ...

    def cancel(self) -> None:
        """Called from another thread to stop the request being handled."""
        ...


class AdapterFactory(Protocol):
    def __call__(self, engine_dir: Path) -> EngineAdapter: ...
