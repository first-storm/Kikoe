"""Isolate SDKs with blocking internals while preserving asynchronous cancellation."""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from contextlib import suppress


class _Worker[T]:
    def __init__(self, operation: Callable[[], Coroutine[None, None, T]]) -> None:
        self.operation = operation
        self.lock = threading.Lock()
        self.cancelled = False
        self.callback: Callable[[], None] | None = None

    def run(self) -> T:
        return asyncio.run(self._run())

    async def _run(self) -> T:
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("An SDK worker needs an asyncio task")

        def cancel_task() -> None:
            loop.call_soon_threadsafe(task.cancel)

        with self.lock:
            if self.cancelled:
                raise asyncio.CancelledError
            self.callback = cancel_task
        try:
            return await self.operation()
        finally:
            with self.lock:
                self.callback = None

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            if self.callback is not None:
                self.callback()


async def run_sdk_worker[T](operation: Callable[[], Coroutine[None, None, T]]) -> T:
    """Wait for SDK cleanup on cancellation, before the caller deletes upload files."""
    worker = _Worker(operation)
    task = asyncio.create_task(asyncio.to_thread(worker.run))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        worker.cancel()
        # Cancellation was requested above; wait for the worker's finally blocks.
        with suppress(asyncio.CancelledError):
            await task
        raise
