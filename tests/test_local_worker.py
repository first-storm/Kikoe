"""The engine worker protocol, the worker loop and the process host."""

import asyncio
import io
import json
import sys
import textwrap
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.local_models import EngineDevice, LocalSettings
from kikoe.core.models import Segment
from kikoe.providers.base import TranscriptionError
from kikoe.providers.local.adapter import Emit, EngineError
from kikoe.providers.local.connection import EngineConnection, parse_segments
from kikoe.providers.local.protocol import (
    ErrorKind,
    Op,
    ProtocolError,
    Reply,
    Request,
    decode_reply,
    decode_request,
    encode_reply,
    encode_request,
)
from kikoe.providers.local.worker_host import WorkerError, WorkerHost, worker_command
from kikoe.providers.local.worker_main import _Worker
from kikoe.storage.local_settings import LocalSettingsStore


def test_requests_and_replies_survive_a_round_trip() -> None:
    request = Request(3, Op.TRANSCRIBE, {"audio": "/tmp/a.wav", "timestamps": True})
    assert decode_request(encode_request(request)) == request
    for reply in (
        Reply(3, {"segments": []}),
        Reply(3, error=ErrorKind.MODEL, message="bad file"),
        Reply(3, event={"text": "hello"}),
    ):
        assert decode_reply(encode_reply(reply)) == reply


@pytest.mark.parametrize(
    "line", [b"not json", b"[]", b'{"op": "probe"}', b'{"id": 1, "op": "dance"}', b'{"id": "1"}']
)
def test_malformed_messages_are_rejected(line: bytes) -> None:
    with pytest.raises(ProtocolError):
        decode_request(line)


def test_an_unknown_error_kind_is_a_plain_failure() -> None:
    reply = decode_reply(b'{"id": 1, "error": {"kind": "new-kind", "message": "x"}}')
    assert reply.error is ErrorKind.FAILED


class FakeAdapter:
    """Answers probes at once and blocks transcriptions until cancelled."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.stop = threading.Event()

    def handle(self, op: Op, args: dict[str, object], emit: Emit) -> dict[str, object]:
        if op is Op.PROBE:
            emit({"step": 1})
            return {"version": "1"}
        if op is Op.STREAM_FEED:
            raise EngineError(ErrorKind.AUDIO, "too short")
        self.started.set()
        self.stop.wait(5)
        raise EngineError(ErrorKind.CANCELLED, "stopped")

    def cancel(self) -> None:
        self.stop.set()


def _replies(out: io.BytesIO) -> list[Reply]:
    return [decode_reply(line) for line in out.getvalue().splitlines()]


def test_the_worker_answers_every_request_and_streams_events() -> None:
    out = io.BytesIO()
    worker = _Worker(out, FakeAdapter)
    worker.receive(Request(1, Op.HELLO))
    worker.receive(Request(2, Op.PROBE))
    worker.receive(Request(3, Op.STREAM_FEED))
    worker.stop()
    worker.compute()
    assert _replies(out) == [
        Reply(1, {"protocol": 1}),
        Reply(2, event={"step": 1}),
        Reply(2, {"version": "1"}),
        Reply(3, error=ErrorKind.AUDIO, message="too short"),
    ]


def test_cancel_stops_the_running_request_and_skips_queued_ones() -> None:
    out = io.BytesIO()
    adapter = FakeAdapter()
    worker = _Worker(out, lambda: adapter)
    thread = threading.Thread(target=worker.compute)
    thread.start()
    worker.receive(Request(1, Op.TRANSCRIBE))
    worker.receive(Request(2, Op.TRANSCRIBE))
    assert adapter.started.wait(5)
    worker.receive(Request(9, Op.CANCEL, {"target": 2}))
    worker.receive(Request(10, Op.CANCEL, {"target": 1}))
    worker.stop()
    thread.join(5)
    by_id = {r.id: r for r in _replies(out)}
    assert by_id[1].error is ErrorKind.CANCELLED
    assert by_id[2].error is ErrorKind.CANCELLED
    assert by_id[9].result == {} and by_id[10].result == {}


def test_an_engine_that_cannot_load_fails_each_request_without_crashing() -> None:
    out = io.BytesIO()

    def broken() -> FakeAdapter:
        raise OSError("libtranscribe.so: cannot open shared object file")

    worker = _Worker(out, broken)
    worker.receive(Request(1, Op.PROBE))
    worker.receive(Request(2, Op.PROBE))
    worker.stop()
    worker.compute()
    assert [r.error for r in _replies(out)] == [ErrorKind.ENGINE, ErrorKind.ENGINE]


FAKE_WORKER = textwrap.dedent(
    """
    import json, sys, time
    mode = sys.argv[1]
    print("native noise", file=sys.stderr, flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        reply = {"id": message["id"]}
        if message["op"] == "hello":
            reply["result"] = {"protocol": 2 if mode == "old" else 1}
        elif message["op"] == "cancel":
            reply["result"] = {}
        elif mode == "crash":
            sys.exit(3)
        elif mode == "stuck":
            time.sleep(60)
        elif mode == "slow":
            for message2 in sys.stdin:
                cancel = json.loads(message2)
                print(json.dumps({"id": cancel["id"], "result": {}}), flush=True)
                break
            reply["error"] = {"kind": "cancelled", "message": ""}
        else:
            print(json.dumps({"id": message["id"], "event": {"partial": "he"}}), flush=True)
            reply["result"] = {"echo": message["args"]}
        print(json.dumps(reply), flush=True)
    """
)


def _host(tmp_path: Path, mode: str) -> WorkerHost:
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    return WorkerHost([sys.executable, str(script), mode])


@run_async
async def test_the_host_returns_results_and_forwards_events(tmp_path: Path) -> None:
    host = _host(tmp_path, "echo")
    events: list[dict[str, object]] = []
    result = await host.request(Op.PROBE, {"x": 1}, events.append)
    assert result == {"echo": {"x": 1}}
    assert events == [{"partial": "he"}]
    assert await host.request(Op.PROBE, {"x": 2}) == {"echo": {"x": 2}}
    await host.close()


@run_async
async def test_a_crashed_worker_fails_the_request_and_restarts_later(tmp_path: Path) -> None:
    host = _host(tmp_path, "crash")
    with pytest.raises(WorkerError) as caught:
        await host.request(Op.PROBE)
    assert caught.value.kind is ErrorKind.ENGINE
    with pytest.raises(WorkerError):
        await host.request(Op.PROBE)


@run_async
async def test_a_worker_of_another_protocol_version_is_refused(tmp_path: Path) -> None:
    host = _host(tmp_path, "old")
    with pytest.raises(WorkerError) as caught:
        await host.request(Op.PROBE)
    assert caught.value.kind is ErrorKind.PROTOCOL


@run_async
async def test_cancelling_asks_the_worker_to_stop(tmp_path: Path) -> None:
    host = _host(tmp_path, "slow")
    task = asyncio.create_task(host.request(Op.TRANSCRIBE))
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await host.close()


@run_async
async def test_a_worker_ignoring_cancel_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("kikoe.providers.local.worker_host.CANCEL_GRACE", 0.3)
    host = _host(tmp_path, "stuck")
    task = asyncio.create_task(host.request(Op.TRANSCRIBE))
    await asyncio.sleep(0.5)
    task.cancel()
    async with asyncio.timeout(5):
        with pytest.raises(asyncio.CancelledError):
            await task
    await host.close()


def test_frozen_builds_start_workers_through_the_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    folder = Path("engines") / "transcribe-cpp"
    assert worker_command("transcribe-cpp", folder)[1:3] == [
        "-m",
        "kikoe.providers.local.worker_main",
    ]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert worker_command("transcribe-cpp", folder)[1:] == [
        "--engine-worker",
        "transcribe-cpp",
        str(folder),
    ]


def test_segments_keep_times_and_speakers_only_when_given() -> None:
    segments = parse_segments(
        [
            {"text": "Hello", "start_ms": 0, "end_ms": 1500, "speaker": 2},
            {"text": "plain"},
            "garbage",
        ]
    )
    assert segments == (
        Segment("Hello", timedelta(0), timedelta(milliseconds=1500), "2"),
        Segment("plain"),
    )


def test_an_unreadable_result_is_a_transcription_error() -> None:
    with pytest.raises(TranscriptionError):
        parse_segments({"segments": "nope"})


class FakeHost(WorkerHost):
    def __init__(self, result: dict[str, object] | WorkerError) -> None:
        super().__init__([])
        self.result = result

    async def request(
        self, op: Op, args: dict[str, object] | None = None, on_event: object = None
    ) -> dict[str, object]:
        if isinstance(self.result, WorkerError):
            raise self.result
        return self.result


@run_async
async def test_probe_lists_devices_and_explains_failures() -> None:
    result: dict[str, object] = {
        "version": "0.4.0",
        "devices": [{"id": "vulkan:1", "kind": "vulkan", "name": "GPU"}, {"id": 5}],
    }
    probe = await EngineConnection(FakeHost(result)).probe()
    assert probe.devices == (EngineDevice("vulkan:1", "vulkan", "GPU"),)
    with pytest.raises(TranscriptionError) as caught:
        await EngineConnection(FakeHost(WorkerError(ErrorKind.ENGINE))).probe()
    assert caught.value.retryable


def test_local_settings_round_trip_and_tolerate_damage(tmp_path: Path) -> None:
    store = LocalSettingsStore(tmp_path / "local.json")
    assert store.load() == LocalSettings()
    settings = LocalSettings().with_device("engine", "vulkan:1")
    store.save(settings)
    assert store.load().device("engine") == "vulkan:1"
    assert settings.with_device("engine", None) == LocalSettings()
    (tmp_path / "local.json").write_text("{broken", encoding="utf-8")
    assert store.load() == LocalSettings()
    (tmp_path / "local.json").write_text(json.dumps({"version": 9}), encoding="utf-8")
    assert store.load() == LocalSettings()
