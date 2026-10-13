"""Share provider request slots across simultaneous transcription jobs."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager


class _Queue:
    def __init__(self) -> None:
        self.condition = asyncio.Condition()
        self.active = 0
        self.waiting = 0


_queues: dict[tuple[asyncio.AbstractEventLoop, str], _Queue] = {}


@asynccontextmanager
async def request_slot(provider_id: str, parallelism: int) -> AsyncGenerator[None]:
    """Each job keeps its chosen cap, and all jobs count requests to the same provider."""
    key = (asyncio.get_running_loop(), provider_id)
    queue = _queues.setdefault(key, _Queue())
    acquired = False
    queue.waiting += 1
    try:
        async with queue.condition:
            await queue.condition.wait_for(lambda: queue.active < parallelism)
            queue.active += 1
            acquired = True
        yield
    finally:
        async with queue.condition:
            queue.waiting -= 1
            if acquired:
                queue.active -= 1
            queue.condition.notify_all()
            if queue.waiting == 0:
                del _queues[key]
