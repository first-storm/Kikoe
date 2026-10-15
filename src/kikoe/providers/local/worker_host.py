"""Run an engine worker process and exchange protocol messages with it.

One host owns at most one process, started on the first request. Each request waits
for its own reply; cancelling the awaiting task asks the worker to stop, and a worker
that does not answer in time is killed. A worker that exits fails every pending
request, and the next request starts a fresh process.
"""

import asyncio
import contextlib
import logging
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from kikoe.providers.local.protocol import (
    PROTOCOL_VERSION,
    ErrorKind,
    Op,
    ProtocolError,
    Reply,
    Request,
    decode_reply,
    encode_request,
)

log = logging.getLogger(__name__)

CANCEL_GRACE = 5.0
"""Seconds a worker gets to stop after a cancel before it is killed."""
_LINE_LIMIT = 64 * 1024 * 1024
"""Replies hold whole transcripts; a long recording easily exceeds asyncio's 64 KiB default."""

type EventCallback = Callable[[dict[str, object]], None]


class WorkerError(Exception):
    """A request failed in the worker, or the worker itself failed."""

    def __init__(self, kind: ErrorKind, message: str = "") -> None:
        super().__init__(message or kind.value)
        self.kind = kind


def worker_command(engine_id: str, engine_dir: Path) -> list[str]:
    """Start the worker with the interpreter Kikoe itself runs on.

    Frozen builds have no ``-m``; their launcher recognises ``--engine-worker`` before
    loading the GUI.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--engine-worker", engine_id, str(engine_dir)]
    return [sys.executable, "-m", "kikoe.providers.local.worker_main", engine_id, str(engine_dir)]


class WorkerHost:
    def __init__(self, command: Sequence[str], env: Mapping[str, str] | None = None) -> None:
        self._command = tuple(command)
        self._env = dict(env) if env is not None else None
        self._process: asyncio.subprocess.Process | None = None
        self._readers: list[asyncio.Task[None]] = []
        self._pending: dict[
            int, tuple[asyncio.Future[dict[str, object]], EventCallback | None]
        ] = {}
        self._next_id = 1
        self._start_lock = asyncio.Lock()

    async def request(
        self, op: Op, args: dict[str, object] | None = None, on_event: EventCallback | None = None
    ) -> dict[str, object]:
        """Send ``op`` and return its result; raises ``WorkerError``."""
        await self._ensure_started()
        future = self._send(op, args or {}, on_event)
        request_id = self._next_id - 1
        try:
            await self._drain()
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            await self._cancel(request_id, future)
            raise

    async def close(self) -> None:
        """Stop the worker; closing stdin lets it exit on its own first."""
        process = self._process
        if process is None:
            return
        self._process = None
        if process.stdin is not None:
            process.stdin.close()
        try:
            async with asyncio.timeout(CANCEL_GRACE):
                await process.wait()
        except TimeoutError:
            _kill(process)
            await process.wait()
        for reader in self._readers:
            await reader
        self._readers.clear()

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self._process is not None and self._process.returncode is None:
                return
            await self._start()
            result = await self._send(Op.HELLO, {}, None)
            if result.get("protocol") != PROTOCOL_VERSION:
                await self.close()
                raise WorkerError(ErrorKind.PROTOCOL, f"worker speaks {result.get('protocol')!r}")

    async def _start(self) -> None:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
                limit=_LINE_LIMIT,
                creationflags=flags,
            )
        except OSError as error:
            raise WorkerError(ErrorKind.ENGINE, f"could not start worker: {error}") from error
        log.info("Started engine worker %d", process.pid)
        self._process = process
        self._readers = [
            asyncio.create_task(self._read_replies(process)),
            asyncio.create_task(self._read_log(process)),
        ]

    def _send(
        self, op: Op, args: dict[str, object], on_event: EventCallback | None
    ) -> asyncio.Future[dict[str, object]]:
        process = self._process
        if process is None or process.stdin is None:
            raise WorkerError(ErrorKind.ENGINE, "the worker is not running")
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = (future, on_event)
        process.stdin.write(encode_request(Request(request_id, op, args)))
        return future

    async def _drain(self) -> None:
        """Wait while the pipe is full; a dead worker is reported through its reply."""
        process = self._process
        if process is not None and process.stdin is not None:
            with contextlib.suppress(ConnectionError):
                await process.stdin.drain()

    async def _cancel(self, request_id: int, future: asyncio.Future[dict[str, object]]) -> None:
        """Ask the worker to stop, and kill it if it does not answer in time."""
        if future.done():
            return
        process = self._process
        try:
            if process is not None and process.stdin is not None:
                self._send(Op.CANCEL, {"target": request_id}, None)
            async with asyncio.timeout(CANCEL_GRACE):
                with contextlib.suppress(WorkerError):
                    await future
        except TimeoutError:
            log.warning("Engine worker did not stop in time; killing it")
            if process is not None:
                _kill(process)
        except WorkerError:
            pass

    async def _read_replies(self, process: asyncio.subprocess.Process) -> None:
        assert process.stdout is not None
        try:
            while line := await process.stdout.readline():
                self._dispatch(line)
        except ValueError, asyncio.LimitOverrunError:
            log.warning("Engine worker sent an oversized reply")
            _kill(process)
        finally:
            await process.wait()
            log.info("Engine worker %d exited with %s", process.pid, process.returncode)
            if self._process is process:
                self._process = None
            failure = WorkerError(ErrorKind.ENGINE, "the engine stopped unexpectedly")
            for future, _callback in self._pending.values():
                if not future.done():
                    future.set_exception(failure)
            self._pending.clear()

    def _dispatch(self, line: bytes) -> None:
        try:
            reply = decode_reply(line)
        except ProtocolError as error:
            log.warning("Ignoring a malformed reply: %s", error)
            return
        entry = self._pending.get(reply.id)
        if entry is None:
            return
        future, callback = entry
        if reply.event is not None:
            if callback is not None and not future.done():
                callback(reply.event)
            return
        del self._pending[reply.id]
        if future.done():
            return
        if reply.error is not None:
            future.set_exception(_error(reply))
        else:
            future.set_result(reply.result or {})

    async def _read_log(self, process: asyncio.subprocess.Process) -> None:
        """Engine output goes to the local log; the formatter redacts its text."""
        assert process.stderr is not None
        with contextlib.suppress(ValueError, asyncio.LimitOverrunError):
            while line := await process.stderr.readline():
                log.debug("Engine: %s", line.decode(errors="replace").rstrip())


def _error(reply: Reply) -> WorkerError:
    assert reply.error is not None
    if reply.message:
        log.warning("Engine request failed (%s): %s", reply.error, reply.message)
    return WorkerError(reply.error, reply.message)


def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        process.kill()
