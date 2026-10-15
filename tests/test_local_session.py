"""Live sessions turn worker stream replies into preview and final transcript events."""

import asyncio
import base64

import pytest

from helpers import run_async
from kikoe.core.models import Segment
from kikoe.core.transcription import TranscriptEvent
from kikoe.providers.base import TranscriptionError
from kikoe.providers.local.protocol import ErrorKind, Op
from kikoe.providers.local.session import LocalSession
from kikoe.providers.local.worker_host import WorkerError, WorkerHost


class StreamHost(WorkerHost):
    """Replies like a streaming engine that recognises one word per fed block."""

    def __init__(self) -> None:
        super().__init__([])
        self.requests: list[tuple[Op, dict[str, object]]] = []
        self.words: list[str] = []
        self.fail: WorkerError | None = None

    async def request(
        self, op: Op, args: dict[str, object] | None = None, on_event: object = None
    ) -> dict[str, object]:
        args = args or {}
        self.requests.append((op, args))
        if self.fail is not None:
            raise self.fail
        match op:
            case Op.STREAM_FEED:
                pcm = args["pcm"]
                assert isinstance(pcm, str)
                if base64.b64decode(pcm) != b"\0\0":
                    self.words.append(f"w{len(self.words)}")
                return {
                    "committed": " ".join(self.words[:-1]),
                    "tentative": " " + self.words[-1] if self.words else "",
                }
            case Op.STREAM_FINISH:
                text = " ".join(self.words)
                self.words = []
                return {"text": text}
            case _:
                return {}


async def _collect(session: LocalSession, into: list[TranscriptEvent]) -> None:
    async for event in session.events():
        into.append(event)


@run_async
async def test_previews_become_one_final_turn_per_flush() -> None:
    host = StreamHost()
    session = await LocalSession.start(host, {"model": "m"})
    events: list[TranscriptEvent] = []
    reader = asyncio.create_task(_collect(session, events))
    await session.send(b"\1\0")
    await session.send(b"\2\0")
    await session.flush()
    await session.send(b"\3\0")
    await session.flush()
    await session.close()
    await reader
    assert [(e.id, e.final, e.segments) for e in events] == [
        ("0", False, (Segment("w0"),)),
        ("0", False, (Segment("w0 w1"),)),
        ("0", True, (Segment("w0 w1"),)),
        ("1", False, (Segment("w0"),)),
        ("1", True, (Segment("w0"),)),
    ]
    assert host.requests[0][0] is Op.STREAM_BEGIN
    assert host.requests[-1] == (Op.STREAM_FINISH, {"restart": False})


@run_async
async def test_flushing_silence_sends_no_event() -> None:
    host = StreamHost()
    session = await LocalSession.start(host, {})
    await session.send(b"\0\0")
    await session.flush()
    assert session._queue.empty()


@run_async
async def test_a_crashed_worker_ends_the_session_with_a_retryable_error() -> None:
    host = StreamHost()
    session = await LocalSession.start(host, {})
    host.fail = WorkerError(ErrorKind.ENGINE)
    with pytest.raises(TranscriptionError) as caught:
        await session.send(b"\1\0")
    assert caught.value.retryable
    with pytest.raises(TranscriptionError):
        async for _event in session.events():
            pass
