import asyncio
from contextlib import suppress

from kikoe.services.upload_queue import request_slot
from tests.helpers import run_async


@run_async
async def test_provider_slots_are_shared_across_jobs_and_other_providers_are_independent() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def other_job() -> None:
        async with request_slot("same", 1):
            entered.set()
            await release.wait()

    async with asyncio.timeout(5):
        async with request_slot("same", 1):
            task = asyncio.create_task(other_job())
            await asyncio.sleep(0)
            assert not entered.is_set()
            async with request_slot("different", 1):
                assert not entered.is_set()
        await entered.wait()
        release.set()
        await task


@run_async
async def test_cancelled_waiter_does_not_consume_or_leak_a_slot() -> None:
    async def waiter() -> None:
        async with request_slot("same", 1):
            raise AssertionError("must remain queued")

    async with asyncio.timeout(5):
        async with request_slot("same", 1):
            task = asyncio.create_task(waiter())
            await asyncio.sleep(0)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        async with request_slot("same", 1):
            pass
