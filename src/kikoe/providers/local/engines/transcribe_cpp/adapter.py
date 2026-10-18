"""transcribe.cpp inside the worker: its Python binding loading the downloaded library.

Install folder layout (from ``catalog.json``)::

    python/transcribe_cpp/...      the pure-Python binding
    native/libtranscribe.so ...    the native library and its ggml backends

The binding has no type information, so values from it are ``Any`` and are converted
to plain protocol data right here.
"""

import array
import base64
import importlib
import logging
import os
import sys
import threading
import wave
from pathlib import Path
from typing import Any

from kikoe.providers.local.adapter import Emit, EngineAdapter, EngineError
from kikoe.providers.local.protocol import ErrorKind, Op

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
_SCALE = 1 / 32768


def create(engine_dir: Path) -> EngineAdapter:
    """Point the binding at the downloaded library, then import it."""
    library = engine_dir / "native" / _library_name()
    if not library.is_file():
        raise EngineError(ErrorKind.ENGINE, f"missing {library.name}")
    os.environ["TRANSCRIBE_LIBRARY"] = str(library)
    sys.path.insert(0, str(engine_dir / "python"))
    return TranscribeCpp(importlib.import_module("transcribe_cpp"))


def _library_name() -> str:
    if sys.platform == "darwin":
        return "libtranscribe.dylib"
    if sys.platform == "win32":
        return "transcribe.dll"
    return "libtranscribe.so"


def device_key(device: Any) -> str:
    """Stable across restarts, unlike the binding's process-local index."""
    return f"{device.kind}:{device.device_id or device.name}"


class TranscribeCpp:
    def __init__(self, binding: Any) -> None:
        self._t = binding
        self._loaded: tuple[str, str | None] | None = None
        self._model: Any = None
        self._session: Any = None
        self._lock = threading.Lock()
        """Guards ``_session`` between the compute thread and ``cancel``."""
        self._stream: Any = None
        self._diarizer: tuple[tuple[str, str | None], Any, Any] | None = None
        """Path and device, model and session of the diarization model, beside the ASR model."""
        self._stream_language: str | None = None

    def handle(self, op: Op, args: dict[str, object], emit: Emit) -> dict[str, object]:
        try:
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
                case Op.DIARIZE:
                    return self._diarize(args)
                case _:
                    raise EngineError(ErrorKind.PROTOCOL, f"unsupported op {op}")
        except self._t.TranscribeError as error:
            raise _failure(self._t, error) from error

    def cancel(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.cancel()
            if self._diarizer is not None:
                self._diarizer[2].cancel()

    def _probe(self) -> dict[str, object]:
        devices: list[dict[str, object]] = [
            {"id": device_key(d), "kind": str(d.kind), "name": str(d.description or d.name)}
            for d in self._t.backends()
        ]
        return {"version": str(self._t.native_version()), "devices": devices}

    def _load(self, path: str, device: str | None) -> Any:
        """Keep the last model loaded; loading a large one takes seconds."""
        if self._loaded == (path, device) and self._model is not None:
            return self._model
        self._unload()
        chosen = self._device(device)
        model = self._t.Model(path, device=chosen) if chosen else self._t.Model(path)
        log.info("Loaded model on %s", model.backend)
        session = model.session()
        with self._lock:
            self._model, self._session = model, session
        self._loaded = (path, device)
        return model

    def _unload(self) -> None:
        self._end_stream()
        with self._lock:
            session, model = self._session, self._model
            self._session = self._model = None
        self._loaded = None
        if session is not None:
            session.close()
        if model is not None:
            model.close()

    def _transcribe(self, args: dict[str, object]) -> dict[str, object]:
        model = self._load(_text(args, "model"), _optional(args, "device"))
        pcm = read_wav(Path(_text(args, "audio")))
        language = _language(model, _optional(args, "language"))
        timestamps = "auto" if args.get("timestamps") else "none"
        diarize = "on" if args.get("diarization") else "off"
        try:
            result = self._session.run(
                pcm, language=language, timestamps=timestamps, diarize=diarize
            )
        except self._t.OutputTruncated as error:
            # Kept like a cloud service's best effort: the text so far is still useful.
            log.warning("Output was truncated: %s", type(error).__name__)
            result = error.partial_result
        reply: dict[str, object] = {"segments": _segments(result)}
        if args.get("words"):
            reply["words"] = _words(result)
        return reply

    def _diarize(self, args: dict[str, object]) -> dict[str, object]:
        path, device = _text(args, "model"), _optional(args, "device")
        if self._diarizer is None or self._diarizer[0] != (path, device):
            chosen = self._device(device)
            model = self._t.Model(path, device=chosen) if chosen else self._t.Model(path)
            if self._t.Role.DIARIZE not in model.roles:
                model.close()
                raise EngineError(ErrorKind.MODEL, "not a diarization model")
            session = model.diarize_session()
            with self._lock:
                old, self._diarizer = self._diarizer, ((path, device), model, session)
            if old is not None:
                old[2].close()
                old[1].close()
        session = self._diarizer[2]
        turns = session.run(read_wav(Path(_text(args, "audio"))))
        return {
            "turns": [
                {"start_ms": int(t.t0_ms), "end_ms": int(t.t1_ms), "speaker": int(t.speaker_id)}
                for t in turns
                if t.t1_ms > t.t0_ms
            ]
        }

    def _device(self, device: str | None) -> Any:
        chosen = next((d for d in self._t.backends() if device_key(d) == device), None)
        if device is not None and chosen is None:
            log.warning("Saved device is not available; choosing automatically")
        return chosen

    def _stream_begin(self, args: dict[str, object]) -> dict[str, object]:
        """A new live stream; one that is still open is discarded first."""
        self._end_stream()
        model = self._load(_text(args, "model"), _optional(args, "device"))
        if not model.capabilities.supports_streaming:
            raise EngineError(ErrorKind.MODEL, "model cannot stream")
        self._stream_language = _language(model, _optional(args, "language"))
        self._stream = self._session.stream(language=self._stream_language)
        return {}

    def _stream_feed(self, args: dict[str, object]) -> dict[str, object]:
        stream = self._require_stream()
        pcm = args.get("pcm")
        if not isinstance(pcm, str):
            raise EngineError(ErrorKind.PROTOCOL, "pcm must be Base64 text")
        stream.feed(pcm_floats(base64.b64decode(pcm)))
        text = stream.text()
        return {"committed": str(text.committed), "tentative": str(text.tentative)}

    def _stream_finish(self, args: dict[str, object]) -> dict[str, object]:
        """Finish the utterance; ``restart`` keeps listening in a fresh stream."""
        stream = self._require_stream()
        stream.finalize()
        text = str(stream.text().display)
        self._end_stream()
        if args.get("restart"):
            self._stream = self._session.stream(language=self._stream_language)
        return {"text": text}

    def _require_stream(self) -> Any:
        if self._stream is None:
            raise EngineError(ErrorKind.PROTOCOL, "no stream is open")
        return self._stream

    def _end_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.reset()
            except self._t.TranscribeError:
                log.warning("Could not reset the stream")


def pcm_floats(data: bytes) -> array.array[float]:
    """Little-endian 16-bit PCM as the float32 samples the binding expects."""
    samples = array.array("h")
    samples.frombytes(data[: len(data) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return array.array("f", (s * _SCALE for s in samples))


def read_wav(path: Path) -> array.array[float]:
    """16 kHz mono 16-bit WAV, as written by Kikoe's upload conversion, as float32."""
    try:
        with wave.open(str(path), "rb") as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (
                SAMPLE_RATE,
                1,
                2,
            ):
                raise EngineError(ErrorKind.AUDIO, "expected 16 kHz mono 16-bit WAV")
            frames = audio.readframes(audio.getnframes())
    except (OSError, wave.Error, EOFError) as error:
        raise EngineError(ErrorKind.AUDIO, f"unreadable audio: {error}") from error
    return pcm_floats(frames)


def _segments(result: Any) -> list[dict[str, object]]:
    timed = result.timestamp_kind not in ("none", "")
    segments: list[dict[str, object]] = []
    for segment in result.segments:
        text = str(segment.text).strip()
        if not text:
            continue
        entry: dict[str, object] = {"text": text}
        if timed:
            entry["start_ms"] = int(segment.t0_ms)
            entry["end_ms"] = int(segment.t1_ms)
        if segment.speaker_id > 0:
            entry["speaker"] = int(segment.speaker_id)
        segments.append(entry)
    if not segments and str(result.text).strip():
        segments.append({"text": str(result.text).strip()})
    return segments


def _words(result: Any) -> list[dict[str, object]]:
    if result.timestamp_kind in ("none", ""):
        return []
    return [
        {"text": str(w.text).strip(), "start_ms": int(w.t0_ms), "end_ms": int(w.t1_ms)}
        for w in result.words
        if str(w.text).strip()
    ]


def _language(model: Any, tag: str | None) -> str | None:
    """The primary subtag, if the model knows it; otherwise let the model detect it."""
    if not tag:
        return None
    code = tag.split("-")[0].lower()
    languages = tuple(model.capabilities.languages)
    if languages and code not in languages:
        log.info("Model does not list the language; detecting it instead")
        return None
    return code


def _failure(binding: Any, error: Exception) -> EngineError:
    kinds = (
        (binding.Aborted, ErrorKind.CANCELLED),
        (binding.InputTooLong, ErrorKind.AUDIO),
        (binding.InputTooShort, ErrorKind.AUDIO),
        (binding.ModelFileNotFound, ErrorKind.MODEL),
        (binding.ModelLoadError, ErrorKind.MODEL),
        (binding.OutOfMemory, ErrorKind.MODEL),
        (binding.BackendError, ErrorKind.DEVICE),
    )
    kind = next((k for cls, k in kinds if isinstance(error, cls)), ErrorKind.FAILED)
    return EngineError(kind, f"{type(error).__name__}: {error}")


def _text(args: dict[str, object], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise EngineError(ErrorKind.PROTOCOL, f"{key} must be a string")
    return value


def _optional(args: dict[str, object], key: str) -> str | None:
    value = args.get(key)
    return value if isinstance(value, str) and value else None
