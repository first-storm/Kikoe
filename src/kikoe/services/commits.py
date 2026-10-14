"""Wait for irreversible writes before their owners release retained resources."""

import asyncio
from collections.abc import Coroutine


async def finish_commit[T](write: Coroutine[None, None, T]) -> T:
    """Cancellation after submission waits for the write and returns its actual result."""
    task = asyncio.create_task(write)
    while not task.done():
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                raise
    return task.result()
