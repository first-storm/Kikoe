"""Mistral Voxtral Realtime over the SDK's WebSocket connection."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack

from mistralai.client.models import (
    RealtimeTranscriptionError,
    TranscriptionStreamDone,
    TranscriptionStreamTextDelta,
)
from mistralai.extra.realtime import RealtimeConnection

from kikoe.core.transcription import TranscriptEvent
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError, close_stack
from kikoe.providers.errors import redact
from kikoe.providers.parsing import parse_text

QUIET_SECONDS = 1.2
"""Mistral acknowledges a flush with no event, so silence after it means the text is complete."""
FLUSH_LIMIT_SECONDS = 8.0
"""Below the recording service's ten second checkpoint timeout."""

type _Item = TranscriptEvent | Exception | None


class MistralSession:
    """Turns are the text between two flushes, since the service sends only text deltas."""

    def __init__(self, connection: RealtimeConnection, stack: AsyncExitStack, key: str) -> None:
        self._connection = connection
        self._stack = stack
        self._key = key
        self._queue: asyncio.Queue[_Item] = asyncio.Queue()
        self._turn = 0
        self._text = ""
        self._dirty = False
        self._consuming = False
        self._last_text = 0.0
        self._pump_task = asyncio.create_task(self._pump())

    async def send(self, pcm: bytes) -> None:
        self._dirty = True
        await self._connection.send_audio(pcm)

    async def flush(self) -> None:
        """Ask for the buffered audio's text, then finalize the turn once it stops arriving."""
        if not self._dirty:
            return
        self._dirty = False
        loop = asyncio.get_running_loop()
        started = self._last_text = loop.time()
        await self._connection.flush_audio()
        while (now := loop.time()) < started + FLUSH_LIMIT_SECONDS:
            quiet = self._last_text + QUIET_SECONDS - now
            if quiet <= 0 or self._pump_task.done():
                break
            await asyncio.sleep(min(quiet, started + FLUSH_LIMIT_SECONDS - now))
        self._finish_turn()
        if self._consuming:
            # The caller checkpoints on return, so the turn's text must be handled by then.
            await self._queue.join()

    async def keep_alive(self) -> None:
        pass  # The SDK's WebSocket answers pings while the microphone is paused.

    async def close(self) -> None:
        self._pump_task.cancel()
        await asyncio.gather(self._pump_task, return_exceptions=True)
        await close_stack(self._stack)

    async def events(self) -> AsyncGenerator[TranscriptEvent]:
        self._consuming = True
        try:
            while True:
                item = await self._queue.get()
                try:
                    if item is None:
                        return
                    if isinstance(item, Exception):
                        raise item
                    yield item
                finally:
                    self._queue.task_done()
        finally:
            self._consuming = False

    async def _pump(self) -> None:
        """Read the connection independently, so flush can wait without consuming events."""
        try:
            async for event in self._connection:
                match event:
                    case TranscriptionStreamTextDelta():
                        self._text += event.text
                        self._last_text = asyncio.get_running_loop().time()
                        self._publish(final=False)
                    case TranscriptionStreamDone():
                        self._finish_turn()
                    case RealtimeTranscriptionError():
                        self._queue.put_nowait(self._error(event))
                        return
                    case _:
                        pass
        except Exception as error:
            self._queue.put_nowait(error)
        finally:
            self._queue.put_nowait(None)

    def _publish(self, *, final: bool) -> None:
        self._queue.put_nowait(TranscriptEvent(str(self._turn), parse_text(self._text), final))

    def _finish_turn(self) -> None:
        if not self._text.strip():
            self._text = ""
            return
        self._publish(final=True)
        self._turn += 1
        self._text = ""

    def _error(self, event: RealtimeTranscriptionError) -> TranscriptionError:
        reason = event.error.message
        if isinstance(reason, str) and reason.strip():
            return TranscriptionError(
                _("Mistral rejected realtime transcription: {message}").format(
                    message=redact(reason.strip(), self._key)
                )
            )
        return TranscriptionError(
            _("The provider rejected realtime transcription. Check the model and API key.")
        )
