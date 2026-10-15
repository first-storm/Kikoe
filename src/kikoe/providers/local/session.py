"""Live microphone transcription with a streaming model in the engine worker.

Each flush ends an utterance: the worker finalizes its stream, returns the text and
starts listening again. The text between two flushes is one turn, shown as a preview
while audio arrives and replaced by the final text at the flush.
"""

import asyncio
import base64
import contextlib
from collections.abc import AsyncGenerator

from kikoe.core.transcription import TranscriptEvent
from kikoe.providers.base import TranscriptionError
from kikoe.providers.local.connection import transcription_error
from kikoe.providers.local.protocol import Op
from kikoe.providers.local.worker_host import WorkerError, WorkerHost
from kikoe.providers.parsing import parse_text

DRAIN_SECONDS = 2.0
"""How long a flush waits for the recording service to read its final event."""

type _Item = TranscriptEvent | TranscriptionError | None


class LocalSession:
    """A ``RealtimeSession`` over one worker; open it with :meth:`start`."""

    def __init__(self, host: WorkerHost) -> None:
        self._host = host
        self._queue: asyncio.Queue[_Item] = asyncio.Queue()
        self._turn = 0
        self._dirty = False
        self._closed = False
        self._shown = ""

    @classmethod
    async def start(cls, host: WorkerHost, args: dict[str, object]) -> LocalSession:
        session = cls(host)
        await session._call(Op.STREAM_BEGIN, args)
        return session

    async def send(self, pcm: bytes) -> None:
        result = await self._call(Op.STREAM_FEED, {"pcm": base64.b64encode(pcm).decode()})
        text = f"{_text(result, 'committed')}{_text(result, 'tentative')}"
        if text.strip() and text != self._shown:
            self._shown = text
            self._dirty = True
            self._queue.put_nowait(TranscriptEvent(str(self._turn), parse_text(text), final=False))

    async def flush(self) -> None:
        """Finish the turn and wait until its final text has been taken."""
        result = await self._call(Op.STREAM_FINISH, {"restart": True})
        text = _text(result, "text")
        if text.strip():
            self._queue.put_nowait(TranscriptEvent(str(self._turn), parse_text(text), final=True))
            self._turn += 1
        elif self._dirty:
            # A preview without final text must not linger as if it were still coming.
            self._queue.put_nowait(TranscriptEvent(str(self._turn), (), final=True))
            self._turn += 1
        self._dirty = False
        self._shown = ""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(DRAIN_SECONDS):
                await self._queue.join()

    async def keep_alive(self) -> None:
        pass  # A local worker has no idle timeout.

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(None)
        # The worker resets the stream when the next one begins; ending it now frees memory.
        with contextlib.suppress(TranscriptionError):
            async with asyncio.timeout(1):
                await self._call(Op.STREAM_FINISH, {"restart": False})

    async def events(self) -> AsyncGenerator[TranscriptEvent]:
        while True:
            item = await self._queue.get()
            try:
                if item is None:
                    return
                if isinstance(item, TranscriptionError):
                    raise item
                yield item
            finally:
                self._queue.task_done()

    async def _call(self, op: Op, args: dict[str, object]) -> dict[str, object]:
        try:
            return await self._host.request(op, args)
        except WorkerError as error:
            failure = transcription_error(error)
            if not self._closed:
                self._queue.put_nowait(failure)
            raise failure from error


def _text(result: dict[str, object], key: str) -> str:
    value = result.get(key, "")
    return value if isinstance(value, str) else ""
