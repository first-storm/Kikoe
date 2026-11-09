"""speech-swift inside the worker: its prebuilt ``speech-server`` on a private port.

Install folder layout (from ``catalog.json``)::

    bin/speech-server, bin/mlx.metallib, bin/*.bundle   the unpacked release

A model folder holds the files speech-swift would download itself, laid out as its
cache expects (``qwen3-speech/models/<org>/<repo>/...``). The server is started with
``QWEN3_CACHE_DIR`` pointing at that folder, so one server serves one model; switching
models restarts it.

``HF_ENDPOINT`` points at ``hub.LocalHub`` in this process, so nothing is downloaded
behind Kikoe's back and loading works offline.

The server only returns plain text, so models have no timestamps. Live audio is kept
here and sent at pauses; each piece's text is committed to the turn.
"""

import atexit
import base64
import contextlib
import http.client
import json
import logging
import os
import socket
import subprocess
import threading
import time
import uuid
from importlib.resources import files
from pathlib import Path
from typing import cast

from kikoe.providers.local.adapter import Emit, EngineAdapter, EngineError
from kikoe.providers.local.engines.speech_swift.hub import CACHE_ROOT, LocalHub, model_repo
from kikoe.providers.local.engines.speech_swift.live import (
    FRAME,
    join_text,
    pause_cut,
    wav_bytes,
)
from kikoe.providers.local.protocol import ErrorKind, Op

log = logging.getLogger(__name__)

START_SECONDS = 60.0
"""How long the server may take to start listening; the first start compiles shaders."""
STOP_SECONDS = 5.0

SERVER_MODELS = {
    "aufklarer/Qwen3-ASR-0.6B-MLX-4bit": "qwen3-asr-0.6b-mlx-int4",
    "aufklarer/Qwen3-ASR-1.7B-MLX-8bit": "qwen3-asr-1.7b-mlx-int8",
    "aufklarer/Parakeet-TDT-v3-CoreML-INT8-30s": "parakeet-tdt-v3-coreml-int8-30s",
    "aufklarer/Parakeet-EOU-120M-CoreML-INT8": "parakeet-eou-120m-coreml-int8",
    "aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-CoreML-INT8": (
        "nemotron-3.5-asr-streaming-0.6b-coreml-int8"
    ),
    "aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8-10s": (
        "omnilingual-asr-ctc-300m-coreml-int8-10s"
    ),
}
"""Hugging Face repository → the server's canonical model name. The server silently
falls back to Parakeet for names it does not know, so only these exact names are sent."""

QWEN3_LANGUAGE_NAMES = {
    "zh": "Chinese",
    "en": "English",
    "yue": "Cantonese",
    "ar": "Arabic",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "pt": "Portuguese",
    "id": "Indonesian",
    "it": "Italian",
    "ko": "Korean",
    "ru": "Russian",
    "th": "Thai",
    "vi": "Vietnamese",
    "ja": "Japanese",
    "tr": "Turkish",
    "hi": "Hindi",
    "ms": "Malay",
    "nl": "Dutch",
    "sv": "Swedish",
    "da": "Danish",
    "fi": "Finnish",
    "pl": "Polish",
    "cs": "Czech",
    "fil": "Filipino",
    "fa": "Persian",
    "el": "Greek",
    "ro": "Romanian",
    "hu": "Hungarian",
    "mk": "Macedonian",
}
"""Qwen3-ASR is prompted with ``language <name>``, the way the model writes it itself."""


def create(engine_dir: Path) -> EngineAdapter:
    server = engine_dir / "bin" / "speech-server"
    if not server.is_file() or not os.access(server, os.X_OK):
        raise EngineError(ErrorKind.ENGINE, f"missing {server.name}")
    return SpeechSwift(engine_dir, server)


class SpeechSwift:
    def __init__(self, engine_dir: Path, server: Path) -> None:
        self._engine_dir = engine_dir
        self._executable = server
        self._languages = catalog_languages()
        self._hub = LocalHub()
        self._server: subprocess.Popen[bytes] | None = None
        self._port = 0
        self._loaded: Path | None = None
        self._repo = ""
        self._lock = threading.Lock()
        """Guards ``_connection`` and ``_cancelled`` between compute thread and ``cancel``."""
        self._connection: http.client.HTTPConnection | None = None
        self._cancelled = False
        self._stream: bytearray | None = None
        self._committed = ""
        self._stream_language: str | None = None
        atexit.register(self.close)

    def handle(self, op: Op, args: dict[str, object], emit: Emit) -> dict[str, object]:
        with self._lock:
            self._cancelled = False
        match op:
            case Op.PROBE:
                return self._probe()
            case Op.TRANSCRIBE:
                return self._transcribe(args)
            case Op.STREAM_BEGIN:
                return self._stream_begin(args)
            case Op.STREAM_FEED:
                return self._stream_feed(args)
            case Op.STREAM_FINISH:
                return self._stream_finish(args)
            case _:
                raise EngineError(ErrorKind.PROTOCOL, f"unsupported op {op}")

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            connection = self._connection
        if connection is not None and connection.sock is not None:
            with contextlib.suppress(OSError):
                connection.sock.shutdown(socket.SHUT_RDWR)

    def close(self) -> None:
        """Stop the server and the hub; also runs when the worker exits."""
        self._stop_server()
        self._hub.close()

    def _probe(self) -> dict[str, object]:
        version = "unknown"
        with contextlib.suppress(OSError, ValueError):
            receipt = json.loads((self._engine_dir / "install.json").read_text(encoding="utf-8"))
            version = str(receipt.get("engine_version", version))
        device: dict[str, object] = {
            "id": "apple-silicon",
            "kind": "metal",
            "name": "Apple Silicon",
        }
        return {"version": version, "devices": [device]}

    def _transcribe(self, args: dict[str, object]) -> dict[str, object]:
        self._load(Path(_text(args, "model")))
        path = Path(_text(args, "audio"))
        try:
            audio = path.read_bytes()
        except OSError as error:
            raise EngineError(ErrorKind.AUDIO, f"unreadable audio: {error}") from error
        text = self._request(audio, _optional(args, "language"))
        return {"segments": [{"text": text}] if text else []}

    def _stream_begin(self, args: dict[str, object]) -> dict[str, object]:
        self._load(Path(_text(args, "model")))
        self._stream_language = _optional(args, "language")
        self._stream = bytearray()
        self._committed = ""
        return {}

    def _stream_feed(self, args: dict[str, object]) -> dict[str, object]:
        stream = self._require_stream()
        pcm = args.get("pcm")
        if not isinstance(pcm, str):
            raise EngineError(ErrorKind.PROTOCOL, "pcm must be Base64 text")
        stream += base64.b64decode(pcm)
        cut = pause_cut(bytes(stream))
        if cut is not None:
            piece = bytes(stream[:cut])
            del stream[:cut]
            text = self._request(wav_bytes(piece), self._stream_language)
            self._committed = join_text(self._committed, text)
        return {"committed": self._committed, "tentative": ""}

    def _stream_finish(self, args: dict[str, object]) -> dict[str, object]:
        """Finish the turn with what is left; ``restart`` keeps listening."""
        stream = self._require_stream()
        text = self._committed
        if len(stream) >= FRAME * 2:
            rest = self._request(wav_bytes(bytes(stream)), self._stream_language)
            text = join_text(text, rest)
        self._stream = bytearray() if args.get("restart") else None
        self._committed = ""
        return {"text": text}

    def _require_stream(self) -> bytearray:
        if self._stream is None:
            raise EngineError(ErrorKind.PROTOCOL, "no stream is open")
        return self._stream

    def _load(self, folder: Path) -> None:
        """Keep the server of the last model running; starting one takes seconds."""
        if self._loaded == folder and self._server is not None and self._server.poll() is None:
            return
        repo = model_repo(folder)
        if repo not in SERVER_MODELS:
            raise EngineError(ErrorKind.MODEL, f"unknown model {repo}")
        self._stop_server()
        self._stream = None
        self._hub.serve(folder)
        self._port = _free_port()
        environment = {
            **os.environ,
            "QWEN3_CACHE_DIR": str(folder),
            "HF_ENDPOINT": f"http://127.0.0.1:{self._hub.port}",
        }
        command = [str(self._executable), "--host", "127.0.0.1", "--port", str(self._port)]
        try:
            # stdout is already the worker's stderr, which goes to Kikoe's log.
            self._server = subprocess.Popen(
                command, cwd=self._executable.parent, env=environment, stdin=subprocess.DEVNULL
            )
        except OSError as error:
            raise EngineError(
                ErrorKind.ENGINE, f"could not start speech-server: {error}"
            ) from error
        log.info("Started speech-server %d for %s", self._server.pid, repo)
        self._wait_until_listening()
        self._loaded, self._repo = folder, repo

    def _wait_until_listening(self) -> None:
        server = self._server
        assert server is not None
        deadline = time.monotonic() + START_SECONDS
        while time.monotonic() < deadline:
            if self._is_cancelled():
                self._stop_server()
                raise EngineError(ErrorKind.CANCELLED, "cancelled while starting")
            code = server.poll()
            if code is not None:
                self._server = None
                raise EngineError(ErrorKind.ENGINE, f"speech-server exited with {code}")
            if self._healthy():
                return
            time.sleep(0.1)
        self._stop_server()
        raise EngineError(ErrorKind.ENGINE, "speech-server did not start in time")

    def _healthy(self) -> bool:
        connection = http.client.HTTPConnection("127.0.0.1", self._port, timeout=1)
        try:
            connection.request("GET", "/health")
            return connection.getresponse().status == 200
        except OSError, http.client.HTTPException:
            return False
        finally:
            connection.close()

    def _stop_server(self) -> None:
        server, self._server = self._server, None
        self._loaded = None
        if server is None or server.poll() is not None:
            return
        server.terminate()
        try:
            server.wait(STOP_SECONDS)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        log.info("Stopped speech-server %d", server.pid)

    def _request(self, audio: bytes, language: str | None) -> str:
        """Transcribe one WAV file's bytes on the running server."""
        fields = {"model": SERVER_MODELS[self._repo], "response_format": "json"}
        if hint := server_language(self._repo, language, self._languages.get(self._repo, ())):
            fields["language"] = hint
        boundary = uuid.uuid4().hex
        body = multipart(fields, audio, boundary)
        connection = http.client.HTTPConnection("127.0.0.1", self._port)
        with self._lock:
            if self._cancelled:
                raise EngineError(ErrorKind.CANCELLED, "cancelled")
            self._connection = connection
        try:
            connection.request(
                "POST",
                "/v1/audio/transcriptions",
                body,
                {"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            response = connection.getresponse()
            status, data = response.status, response.read()
        except (OSError, http.client.HTTPException) as error:
            raise self._connection_failure(error) from error
        finally:
            with self._lock:
                self._connection = None
            connection.close()
        return _reply_text(status, data)

    def _connection_failure(self, error: Exception) -> EngineError:
        if self._is_cancelled():
            return EngineError(ErrorKind.CANCELLED, "cancelled")
        if self._server is None or self._server.poll() is not None:
            self._stop_server()
            return EngineError(ErrorKind.ENGINE, f"speech-server stopped: {error}")
        return EngineError(ErrorKind.FAILED, f"{type(error).__name__}: {error}")

    def _is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled


def catalog_languages() -> dict[str, tuple[str, ...]]:
    """Repository → language codes, from this engine's catalog: one list to maintain."""
    data = json.loads(files(__package__).joinpath("catalog.json").read_text(encoding="utf-8"))
    languages: dict[str, tuple[str, ...]] = {}
    for model in data.get("models", []):
        target = model["files"][0]["target"]
        parts = target.split("/")
        repo = "/".join(parts[len(CACHE_ROOT.parts) : len(CACHE_ROOT.parts) + 2])
        languages[repo] = tuple(model.get("languages", ()))
    return languages


def server_language(repo: str, tag: str | None, supported: tuple[str, ...]) -> str | None:
    """The hint the server's model understands, or None to let it detect the language."""
    if not tag or not supported:
        return None
    code = tag.split("-")[0].lower()
    if code not in supported:
        log.info("Model does not list the language; detecting it instead")
        return None
    if SERVER_MODELS.get(repo, "").startswith("qwen3-asr"):
        return QWEN3_LANGUAGE_NAMES.get(code)
    return code


def multipart(fields: dict[str, str], audio: bytes, boundary: str) -> bytes:
    """A ``multipart/form-data`` body with text fields and the WAV as ``file``."""
    parts: list[bytes] = []
    for name, value in fields.items():
        header = f'Content-Disposition: form-data; name="{name}"'
        parts.append(f"--{boundary}\r\n{header}\r\n\r\n{value}\r\n".encode())
    parts.append(
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode()
    )
    parts.extend((audio, f"\r\n--{boundary}--\r\n".encode()))
    return b"".join(parts)


def _reply_text(status: int, data: bytes) -> str:
    try:
        reply = _object(json.loads(data))
    except ValueError:
        reply = {}
    text = reply.get("text")
    if status == 200 and isinstance(text, str):
        return text.strip()
    error = _object(reply.get("error"))
    message = error.get("message")
    detail = message if isinstance(message, str) and message else f"HTTP {status}"
    if status == 200:
        raise EngineError(ErrorKind.FAILED, "unreadable reply")
    if status in (400, 413, 415, 422):
        raise EngineError(ErrorKind.AUDIO, detail)
    raise EngineError(ErrorKind.MODEL, detail)


def _object(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _text(args: dict[str, object], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise EngineError(ErrorKind.PROTOCOL, f"{key} must be a string")
    return value


def _optional(args: dict[str, object], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) and value else None
