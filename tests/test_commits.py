"""A cancelled owner must wait until its irreversible write has finished."""

import asyncio

import pytest

from helpers import run_async
from kikoe.services.commits import finish_commit


@run_async
async def test_commit_survives_repeated_owner_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def write() -> str:
        started.set()
        await release.wait()
        return "saved"

    owner = asyncio.create_task(finish_commit(write()))
    await started.wait()
    owner.cancel()
    await asyncio.sleep(0)
    owner.cancel()
    await asyncio.sleep(0)
    assert not owner.done()
    release.set()
    assert await owner == "saved"


@run_async
async def test_commit_reports_failure_after_owner_cancellation() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def write() -> None:
        started.set()
        await release.wait()
        raise ValueError("write failed")

    owner = asyncio.create_task(finish_commit(write()))
    await started.wait()
    owner.cancel()
    release.set()
    with pytest.raises(ValueError, match="write failed"):
        await owner


@run_async
async def test_commit_preserves_write_cancellation() -> None:
    async def write() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await finish_commit(write())
