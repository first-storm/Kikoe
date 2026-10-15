"""Messages between Kikoe and an engine worker: one JSON object per line.

The app sends requests on the worker's stdin; the worker answers on its stdout with
exactly one reply per request, optionally preceded by events for that request (stream
updates). Audio for files travels as a WAV path; live audio as Base64 PCM.
Both sides import only this module, so neither depends on the other's internals.
"""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

PROTOCOL_VERSION = 1


class Op(StrEnum):
    HELLO = "hello"
    """Reply carries the protocol version; sent first by the host."""
    PROBE = "probe"
    """Reply: engine version and the devices it can run on."""
    TRANSCRIBE = "transcribe"
    CANCEL = "cancel"
    """Stop request ``target``; it then fails with ``ErrorKind.CANCELLED``."""
    STREAM_BEGIN = "stream-begin"
    STREAM_FEED = "stream-feed"
    STREAM_FINISH = "stream-finish"
    DIARIZE = "diarize"


class ErrorKind(StrEnum):
    CANCELLED = "cancelled"
    ENGINE = "engine"
    """The engine's native library could not be loaded."""
    MODEL = "model"
    """The model file could not be loaded, e.g. it is damaged or needs more memory."""
    DEVICE = "device"
    """The chosen device is gone or cannot run this model."""
    AUDIO = "audio"
    """The audio was rejected, e.g. too long or empty."""
    PROTOCOL = "protocol"
    FAILED = "failed"


class ProtocolError(ValueError):
    """A line that is not a valid message; the other side is broken or not a worker."""


@dataclass(frozen=True, slots=True)
class Request:
    id: int
    op: Op
    args: dict[str, object] = field(default_factory=dict[str, object])


@dataclass(frozen=True, slots=True)
class Reply:
    """A final answer when ``event`` is None, otherwise an update for request ``id``."""

    id: int
    result: dict[str, object] | None = None
    error: ErrorKind | None = None
    message: str = ""
    """Developer detail for the log; never shown to the user as is."""
    event: dict[str, object] | None = None


def encode_request(request: Request) -> bytes:
    data: dict[str, object] = {"id": request.id, "op": request.op.value, "args": request.args}
    return _line(data)


def decode_request(line: bytes | str) -> Request:
    data = _object(line)
    try:
        op = Op(cast(str, data.get("op")))
    except ValueError as error:
        raise ProtocolError(f"unknown op {data.get('op')!r}") from error
    args = data.get("args", {})
    if not isinstance(args, dict):
        raise ProtocolError("args must be an object")
    return Request(_id(data), op, cast(dict[str, object], args))


def encode_reply(reply: Reply) -> bytes:
    data: dict[str, object] = {"id": reply.id}
    if reply.event is not None:
        data["event"] = reply.event
    elif reply.error is not None:
        data["error"] = {"kind": reply.error.value, "message": reply.message}
    else:
        data["result"] = reply.result or {}
    return _line(data)


def decode_reply(line: bytes | str) -> Reply:
    data = _object(line)
    reply_id = _id(data)
    if "event" in data:
        return Reply(reply_id, event=_dict(data["event"], "event"))
    if "error" in data:
        error = _dict(data["error"], "error")
        try:
            kind = ErrorKind(cast(str, error.get("kind")))
        except ValueError:
            kind = ErrorKind.FAILED
        message = error.get("message", "")
        return Reply(reply_id, error=kind, message=message if isinstance(message, str) else "")
    return Reply(reply_id, result=_dict(data.get("result", {}), "result"))


def _line(data: dict[str, object]) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def _object(line: bytes | str) -> dict[str, object]:
    try:
        data: object = json.loads(line)
    except ValueError as error:
        raise ProtocolError("not JSON") from error
    return _dict(data, "message")


def _dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{what} must be an object")
    return cast(dict[str, object], value)


def _id(data: dict[str, object]) -> int:
    value = data.get("id")
    if type(value) is not int:
        raise ProtocolError("id must be an integer")
    return value
