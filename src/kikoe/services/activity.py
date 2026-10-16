"""Track the background work a window owns, and wind it down when the window closes."""

import asyncio
from collections.abc import Awaitable, Callable

from kikoe.core.activity import Stage


class ActivityTracker:
    """The running tasks of one window; reading them decides what the window may start.

    Plain mutable state on purpose: workflows start and finish at different times and
    the window derives its action states from the combination.
    """

    def __init__(self) -> None:
        self.import_task: asyncio.Task[None] | None = None
        """A file transcription: import, retry or re-transcription."""
        self.recording_task: asyncio.Task[None] | None = None
        self.recovery_task: asyncio.Task[None] | None = None
        self.stage = Stage.IDLE
        """The foreground step; recovery that is writing audio must finish, not be cancelled."""
        self.closing = False

    @property
    def recovery_pending(self) -> bool:
        """The startup recovery check or the recovery itself has not finished."""
        return self.recovery_task is not None and not self.recovery_task.done()

    def has_pending_work(self) -> bool:
        """Whether closing now would interrupt a recording, a transcription or a recovery."""
        return (
            self.recording_task is not None or self.import_task is not None or self.recovery_pending
        )

    async def wind_down(
        self,
        *,
        wait_edits: Callable[[], Awaitable[None]],
        finish_playback: Callable[[], Awaitable[None]],
        stop_recording: Callable[[], Awaitable[None]],
    ) -> None:
        """Save pending edits, stop the microphone and end running work, in that order.

        Recording is stopped rather than cancelled so its audio is kept. A recovery that
        is already writing audio is awaited; one that has not started is cancelled.
        Tasks are read at the moment they are needed because each step takes time.
        """
        await wait_edits()
        await finish_playback()
        recording = self.recording_task
        await stop_recording()
        importing = self.import_task
        if importing is not None and not importing.cancelling():
            importing.cancel()
        recovery = self.recovery_task
        if recovery is not None and not recovery.done():
            if self.stage is not Stage.RECOVERING:
                recovery.cancel()
            await _await_cancelled_by_us(recovery)
        if recording is not None:
            await recording
        elif importing is not None:
            await _await_cancelled_by_us(importing)


async def _await_cancelled_by_us(task: asyncio.Task[None]) -> None:
    """Wait for ``task``; swallow its own cancellation but not ours."""
    try:
        await task
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
