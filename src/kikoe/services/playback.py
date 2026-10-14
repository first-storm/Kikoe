"""Serialize output-device access and wait for cancelled playback cleanup."""

import asyncio
from collections.abc import Callable, Coroutine
from datetime import timedelta
from pathlib import Path

from kikoe.media.player import PlaybackError, play_audio


class Playback:
    """Own playback tasks while the UI owns highlighting and error presentation."""

    def __init__(
        self,
        start_task: Callable[[Coroutine[None, None, None]], asyncio.Task[None]],
        on_changed: Callable[[str | None], None],
        on_error: Callable[[PlaybackError], None],
    ) -> None:
        self._start_task = start_task
        self._on_changed = on_changed
        self._on_error = on_error
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._key: tuple[str, float, float | None] | None = None

    @property
    def pending(self) -> bool:
        return self._task is not None and not self._task.done()

    def play(
        self, path: Path, recording_id: str, start: float, end: float | None, device: str | None
    ) -> None:
        """Selecting the same range twice stops playback; another range replaces it."""
        key = recording_id, start, end
        previous = self._task
        same = key == self._key
        self.stop()
        if same:
            return
        self._key = key
        self._on_changed(recording_id if end is None else None)
        self._task = self._start_task(self._play(path, key, device, previous))

    def stop(self) -> None:
        if self._task is not None and not self._task.done() and not self._task.cancelling():
            self._task.cancel()
        self._key = None
        self._on_changed(None)

    async def finish(self) -> None:
        """Release the output device before capture, transcription or shutdown."""
        self.stop()
        await _wait_cancelled(self._task)
        async with self._lock:
            pass

    async def _play(
        self,
        path: Path,
        key: tuple[str, float, float | None],
        device: str | None,
        previous: asyncio.Task[None] | None,
    ) -> None:
        try:
            await _wait_cancelled(previous)
            async with self._lock:
                await play_audio(
                    path,
                    start=timedelta(seconds=key[1]),
                    end=timedelta(seconds=key[2]) if key[2] is not None else None,
                    device=device,
                )
        except PlaybackError as error:
            self._on_error(error)
        finally:
            if self._task is asyncio.current_task():
                self._key = None
                self._on_changed(None)


async def _wait_cancelled(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise
