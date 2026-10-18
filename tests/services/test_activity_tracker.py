"""Closing a window ends its work in an order that never loses a recording."""

import asyncio

import pytest

from kikoe.core.activity import Stage
from kikoe.services.activity import ActivityTracker
from tests.helpers import run_async


class Steps:
    """Records the order in which the shutdown steps ran."""

    def __init__(self) -> None:
        self.log: list[str] = []

    def step(self, name: str):  # a tiny coroutine factory used only here
        async def run() -> None:
            self.log.append(name)

        return run


async def _forever() -> None:
    await asyncio.Event().wait()


def never_finishing() -> asyncio.Task[None]:
    return asyncio.ensure_future(_forever())


async def wind_down(tracker: ActivityTracker, steps: Steps) -> None:
    await tracker.wind_down(
        wait_edits=steps.step("edits"),
        finish_playback=steps.step("playback"),
        stop_recording=steps.step("stop"),
    )


def test_an_idle_tracker_has_no_pending_work() -> None:
    assert not ActivityTracker().has_pending_work()


@run_async
async def test_each_kind_of_running_work_is_pending() -> None:
    for name in ("recording_task", "import_task", "recovery_task"):
        tracker = ActivityTracker()
        task = never_finishing()
        setattr(tracker, name, task)
        assert tracker.has_pending_work()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@run_async
async def test_a_finished_recovery_check_is_not_pending() -> None:
    tracker = ActivityTracker()
    tracker.recovery_task = asyncio.ensure_future(asyncio.sleep(0))
    await tracker.recovery_task
    assert not tracker.recovery_pending
    assert not tracker.has_pending_work()


@run_async
async def test_edits_and_playback_finish_before_the_microphone_stops() -> None:
    steps = Steps()
    await wind_down(ActivityTracker(), steps)
    assert steps.log == ["edits", "playback", "stop"]


@run_async
async def test_the_recording_task_is_awaited_after_it_is_stopped() -> None:
    tracker = ActivityTracker()
    stopped = asyncio.Event()
    finished: list[str] = []

    async def record() -> None:
        await stopped.wait()
        finished.append("recording")

    tracker.recording_task = asyncio.ensure_future(record())

    async def stop() -> None:
        finished.append("stop")
        stopped.set()

    await tracker.wind_down(
        wait_edits=Steps().step("edits"),
        finish_playback=Steps().step("playback"),
        stop_recording=stop,
    )
    assert finished == ["stop", "recording"]


@run_async
async def test_a_running_import_is_cancelled_and_its_cancellation_is_absorbed() -> None:
    tracker = ActivityTracker()
    tracker.import_task = never_finishing()
    await wind_down(tracker, Steps())
    assert tracker.import_task.cancelled()


@run_async
async def test_an_import_alongside_a_recording_is_cancelled_but_the_recording_is_awaited() -> None:
    tracker = ActivityTracker()
    tracker.import_task = never_finishing()
    done = asyncio.Event()

    async def record() -> None:
        done.set()

    tracker.recording_task = asyncio.ensure_future(record())
    await wind_down(tracker, Steps())
    assert tracker.import_task.cancelled()
    assert done.is_set()


@run_async
async def test_a_recovery_that_has_not_started_writing_is_cancelled() -> None:
    tracker = ActivityTracker()
    tracker.recovery_task = never_finishing()
    await wind_down(tracker, Steps())
    assert tracker.recovery_task.cancelled()


@run_async
async def test_a_recovery_that_is_writing_audio_is_allowed_to_finish() -> None:
    tracker = ActivityTracker()
    tracker.stage = Stage.RECOVERING
    release = asyncio.Event()
    result: list[str] = []

    async def recover() -> None:
        await release.wait()
        result.append("recovered")

    tracker.recovery_task = asyncio.ensure_future(recover())
    closing = asyncio.ensure_future(wind_down(tracker, Steps()))
    await asyncio.sleep(0.01)
    assert not closing.done()
    release.set()
    await closing
    assert result == ["recovered"]


@run_async
async def test_a_failing_recording_task_reports_its_error_to_the_caller() -> None:
    tracker = ActivityTracker()

    async def record() -> None:
        raise RuntimeError("disk full")

    tracker.recording_task = asyncio.ensure_future(record())
    with pytest.raises(RuntimeError, match="disk full"):
        await wind_down(tracker, Steps())


@run_async
async def test_closing_during_cancel_does_not_interrupt_resource_cleanup() -> None:
    tracker = ActivityTracker()
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()

    async def importing() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    task = asyncio.create_task(importing())
    tracker.import_task = task
    await started.wait()
    task.cancel()
    await cleaning.wait()
    closing = asyncio.create_task(wind_down(tracker, Steps()))
    await asyncio.sleep(0)
    release.set()
    async with asyncio.timeout(1):
        await closing
    assert cleaned.is_set()
    assert task.cancelled()
