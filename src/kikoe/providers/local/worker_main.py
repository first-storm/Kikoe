"""Entry point of an engine worker process: ``<python> -m kikoe.providers.local.worker_main``.

Arguments are the engine ID and its install folder. Requests are read on the main
thread so a cancel arrives while the compute thread is busy; requests run one at a
time in order. The worker exits when its stdin closes, which also happens when the
app dies, so no worker outlives Kikoe.

Native libraries may print to stdout, so the protocol uses a private copy of the
original stdout and fd 1 is pointed at stderr before any engine code loads.
"""

import contextlib
import logging
import os
import queue
import sys
import threading
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import BinaryIO, cast

from kikoe.providers.local.adapter import AdapterFactory, EngineAdapter, EngineError
from kikoe.providers.local.engines import ENGINES
from kikoe.providers.local.protocol import (
    PROTOCOL_VERSION,
    ErrorKind,
    Op,
    ProtocolError,
    Reply,
    Request,
    decode_request,
    encode_reply,
)

log = logging.getLogger(__name__)


class _Worker:
    def __init__(self, out: BinaryIO, create: Callable[[], EngineAdapter]) -> None:
        self._out = out
        self._write_lock = threading.Lock()
        self._create = create
        self._adapter: EngineAdapter | None = None
        self._failure: EngineError | None = None
        self._queue: queue.Queue[Request | None] = queue.Queue()
        self._lock = threading.Lock()
        self._running: int | None = None
        self._queued: set[int] = set()
        self._cancelled: set[int] = set()

    def send(self, reply: Reply) -> None:
        with self._write_lock:
            self._out.write(encode_reply(reply))
            self._out.flush()

    def receive(self, request: Request) -> None:
        """Runs on the reader thread: answer control messages now, queue the rest."""
        match request.op:
            case Op.HELLO:
                self.send(Reply(request.id, {"protocol": PROTOCOL_VERSION}))
            case Op.CANCEL:
                self._cancel(request.args.get("target"))
                self.send(Reply(request.id, {}))
            case _:
                with self._lock:
                    self._queued.add(request.id)
                self._queue.put(request)

    def _cancel(self, target: object) -> None:
        with self._lock:
            if target == self._running and self._adapter is not None:
                self._adapter.cancel()
            elif target in self._queued and isinstance(target, int):
                self._cancelled.add(target)

    def stop(self) -> None:
        """Input ended: cancel the current request so the process can exit promptly."""
        with self._lock:
            if self._running is not None and self._adapter is not None:
                self._adapter.cancel()
        self._queue.put(None)

    def compute(self) -> None:
        while (request := self._queue.get()) is not None:
            with self._lock:
                self._queued.discard(request.id)
                if request.id in self._cancelled:
                    self._cancelled.discard(request.id)
                    self.send(Reply(request.id, error=ErrorKind.CANCELLED))
                    continue
                self._running = request.id
            try:
                self.send(Reply(request.id, self._handle(request)))
            except EngineError as failure:
                self.send(Reply(request.id, error=failure.kind, message=str(failure)))
            except Exception as error:  # A worker must answer every request, whatever happens.
                log.exception("Request %s failed", request.op)
                self.send(Reply(request.id, error=ErrorKind.FAILED, message=repr(error)))
            finally:
                with self._lock:
                    self._running = None
                    self._cancelled.discard(request.id)

    def _handle(self, request: Request) -> dict[str, object]:
        adapter = self._load()
        return adapter.handle(
            request.op, request.args, lambda e: self.send(Reply(request.id, event=e))
        )

    def _load(self) -> EngineAdapter:
        if self._failure is not None:
            raise self._failure
        if self._adapter is None:
            try:
                adapter = self._create()
            except EngineError as failure:
                self._failure = failure
                raise
            except Exception as error:  # Any import or loader failure means the same to the app.
                log.exception("Could not load the engine")
                self._failure = EngineError(ErrorKind.ENGINE, repr(error))
                raise self._failure from error
            with self._lock:
                self._adapter = adapter
        return self._adapter


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in ENGINES:
        print("usage: worker_main <engine id> <engine folder>", file=sys.stderr)  # noqa: T201 - CLI usage
        return 2
    engine_id, folder = args
    logging.basicConfig(level=os.environ.get("KIKOE_LOG_LEVEL", "INFO").upper(), stream=sys.stderr)
    out = _protocol_output()
    module = import_module(f"{ENGINES[engine_id]}.adapter")
    create = cast(AdapterFactory, module.create)
    worker = _Worker(out, lambda: create(Path(folder)))
    compute = threading.Thread(target=worker.compute, name="engine-compute", daemon=True)
    compute.start()
    for line in sys.stdin.buffer:
        if not line.strip():
            continue
        try:
            request = decode_request(line)
        except ProtocolError as error:
            log.warning("Ignoring a malformed request: %s", error)
            continue
        worker.receive(request)
    worker.stop()
    compute.join(timeout=5)
    out.flush()
    # The compute thread may be stuck in native code; do not wait for it any longer.
    os._exit(0)


def _protocol_output() -> BinaryIO:
    """Keep the real stdout for messages and send anything else printed to stderr."""
    sys.stdout.flush()
    descriptor = os.dup(1)
    os.dup2(2, 1)
    with contextlib.suppress(AttributeError, ValueError):
        sys.stdout = sys.stderr
    return os.fdopen(descriptor, "wb", buffering=0)


if __name__ == "__main__":
    sys.exit(main())
