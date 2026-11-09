"""The speech-swift adapter against a fake ``speech-server``; no binary, no network."""

import base64
import json
import math
import os
import sys
import textwrap
import threading
import time
from array import array
from collections.abc import Iterator
from pathlib import Path

import pytest

from kikoe.providers.local.adapter import EngineError
from kikoe.providers.local.engines import load_catalog
from kikoe.providers.local.engines.speech_swift.adapter import (
    SERVER_MODELS,
    SpeechSwift,
    catalog_languages,
    create,
    multipart,
    server_language,
)
from kikoe.providers.local.engines.speech_swift.hub import LocalHub, hub_route
from kikoe.providers.local.engines.speech_swift.live import (
    SAMPLE_RATE,
    join_text,
    pause_cut,
    wav_bytes,
)
from kikoe.providers.local.protocol import ErrorKind, Op

PARAKEET = "aufklarer/Parakeet-EOU-120M-CoreML-INT8"
QWEN = "aufklarer/Qwen3-ASR-0.6B-MLX-4bit"

FAKE_SERVER = textwrap.dedent(
    """
    import json, os, sys, time, urllib.request
    from http.server import BaseHTTPRequestHandler, HTTPServer

    mode = os.environ.get("FAKE_MODE", "ok")
    log = os.environ["FAKE_LOG"]
    port = int(sys.argv[sys.argv.index("--port") + 1])
    if mode == "exit":
        sys.exit(4)

    def record(entry):
        with open(log, "a") as out:
            out.write(json.dumps(entry) + "\\n")

    class Handler(BaseHTTPRequestHandler):
        count = 0

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"])).decode("latin-1")
            hub = os.environ["HF_ENDPOINT"]
            repo = os.environ["FAKE_REPO"]
            url = f"{hub}/api/models/{repo}/tree/main?recursive=true&expand=true"
            listing = json.load(urllib.request.urlopen(url))
            record({"pid": os.getpid(), "body": body, "listing": listing,
                    "cache": os.environ["QWEN3_CACHE_DIR"]})
            if mode == "slow":
                time.sleep(30)
            Handler.count += 1
            if mode == "error":
                status, reply = 500, {"error": {"message": "Transcription failed: boom"}}
            else:
                status, reply = 200, {"text": f" piece {Handler.count} "}
            data = json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    """
)


def _engine(tmp_path: Path) -> Path:
    engine = tmp_path / "engine"
    server = engine / "bin" / "speech-server"
    server.parent.mkdir(parents=True)
    server.write_text(f"#!{sys.executable}\n{FAKE_SERVER}", encoding="utf-8")
    server.chmod(0o755)
    (engine / "install.json").write_text('{"engine_version": "0.0.29"}', encoding="utf-8")
    return engine


def _model(tmp_path: Path, repo: str) -> Path:
    folder = tmp_path / "models" / repo.rsplit("/", 1)[1]
    files = folder / "qwen3-speech" / "models" / repo
    (files / "encoder.mlmodelc" / "weights").mkdir(parents=True)
    (files / "config.json").write_text("{}", encoding="utf-8")
    (files / "encoder.mlmodelc" / "weights" / "weight.bin").write_bytes(b"12345")
    return folder


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[SpeechSwift, Path]]:
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "requests.jsonl"))
    monkeypatch.setenv("FAKE_REPO", PARAKEET)
    adapter = create(_engine(tmp_path))
    assert isinstance(adapter, SpeechSwift)
    yield adapter, tmp_path
    adapter.close()


def _requests(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _wav(tmp_path: Path, seconds: float = 1.0) -> Path:
    path = tmp_path / "audio.wav"
    path.write_bytes(wav_bytes(_tone(seconds)))
    return path


def _tone(seconds: float, level: int = 8000) -> bytes:
    count = int(seconds * SAMPLE_RATE)
    return array("h", (int(level * math.sin(i / 8)) for i in range(count))).tobytes()


def _silence(seconds: float) -> bytes:
    return bytes(int(seconds * SAMPLE_RATE) * 2)


def test_files_go_to_the_server_by_its_exact_model_name(fake: tuple[SpeechSwift, Path]) -> None:
    adapter, tmp_path = fake
    folder = _model(tmp_path, PARAKEET)
    args: dict[str, object] = {"model": str(folder), "audio": str(_wav(tmp_path))}
    result = adapter.handle(Op.TRANSCRIBE, args, lambda _event: None)
    assert result == {"segments": [{"text": "piece 1"}]}
    (request,) = _requests(tmp_path)
    assert 'name="model"\r\n\r\nparakeet-eou-120m-coreml-int8\r\n' in str(request["body"])
    assert 'name="language"' not in str(request["body"])
    assert request["cache"] == str(folder)


def test_the_local_hub_lists_exactly_the_installed_files(fake: tuple[SpeechSwift, Path]) -> None:
    adapter, tmp_path = fake
    args: dict[str, object] = {
        "model": str(_model(tmp_path, PARAKEET)),
        "audio": str(_wav(tmp_path)),
    }
    adapter.handle(Op.TRANSCRIBE, args, lambda _event: None)
    (request,) = _requests(tmp_path)
    assert request["listing"] == [
        {"type": "file", "path": "config.json", "size": 2},
        {"type": "file", "path": "encoder.mlmodelc/weights/weight.bin", "size": 5},
    ]


def test_supported_languages_are_sent_as_the_model_expects(fake: tuple[SpeechSwift, Path]) -> None:
    adapter, tmp_path = fake
    args: dict[str, object] = {
        "model": str(_model(tmp_path, PARAKEET)),
        "audio": str(_wav(tmp_path)),
        "language": "en-GB",
    }
    adapter.handle(Op.TRANSCRIBE, args, lambda _event: None)
    (request,) = _requests(tmp_path)
    assert 'name="language"\r\n\r\nen\r\n' in str(request["body"])


@pytest.mark.parametrize(
    ("repo", "tag", "supported", "expected"),
    [
        (QWEN, "zh-Hans", ("zh", "en"), "Chinese"),
        (QWEN, "yue", ("yue",), "Cantonese"),
        (PARAKEET, "en-US", ("en",), "en"),
        (PARAKEET, "fr", ("en",), None),
        (PARAKEET, None, ("en",), None),
        ("aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8-10s", "de", (), None),
    ],
)
def test_language_hints_match_each_model(
    repo: str, tag: str | None, supported: tuple[str, ...], expected: str | None
) -> None:
    assert server_language(repo, tag, supported) == expected


def test_a_server_failure_is_a_model_error(
    fake: tuple[SpeechSwift, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "error")
    adapter, tmp_path = fake
    args: dict[str, object] = {
        "model": str(_model(tmp_path, PARAKEET)),
        "audio": str(_wav(tmp_path)),
    }
    with pytest.raises(EngineError) as caught:
        adapter.handle(Op.TRANSCRIBE, args, lambda _event: None)
    assert caught.value.kind is ErrorKind.MODEL
    assert "boom" in str(caught.value)


def test_a_server_that_exits_on_start_is_an_engine_error(
    fake: tuple[SpeechSwift, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "exit")
    adapter, tmp_path = fake
    args: dict[str, object] = {
        "model": str(_model(tmp_path, PARAKEET)),
        "audio": str(_wav(tmp_path)),
    }
    with pytest.raises(EngineError) as caught:
        adapter.handle(Op.TRANSCRIBE, args, lambda _event: None)
    assert caught.value.kind is ErrorKind.ENGINE


def test_cancel_interrupts_a_running_request(
    fake: tuple[SpeechSwift, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_MODE", "slow")
    adapter, tmp_path = fake
    args: dict[str, object] = {
        "model": str(_model(tmp_path, PARAKEET)),
        "audio": str(_wav(tmp_path)),
    }

    def cancel_once_sent() -> None:
        for _ in range(200):
            if _requests(tmp_path):
                break
            time.sleep(0.05)
        adapter.cancel()

    canceller = threading.Thread(target=cancel_once_sent)
    canceller.start()
    started = time.monotonic()
    with pytest.raises(EngineError) as caught:
        adapter.handle(Op.TRANSCRIBE, args, lambda _event: None)
    canceller.join()
    assert caught.value.kind is ErrorKind.CANCELLED
    assert time.monotonic() - started < 15


def test_switching_models_restarts_the_server(
    fake: tuple[SpeechSwift, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, tmp_path = fake
    audio = str(_wav(tmp_path))
    first, second = _model(tmp_path, PARAKEET), _model(tmp_path, QWEN)
    adapter.handle(Op.TRANSCRIBE, {"model": str(first), "audio": audio}, lambda _event: None)
    adapter.handle(Op.TRANSCRIBE, {"model": str(first), "audio": audio}, lambda _event: None)
    monkeypatch.setenv("FAKE_REPO", QWEN)
    adapter.handle(Op.TRANSCRIBE, {"model": str(second), "audio": audio}, lambda _event: None)
    pids = [r["pid"] for r in _requests(tmp_path)]
    assert pids[0] == pids[1] != pids[2]
    assert "qwen3-asr-0.6b-mlx-int4" in str(_requests(tmp_path)[2]["body"])


def test_live_audio_is_committed_at_pauses_and_finished_at_flushes(
    fake: tuple[SpeechSwift, Path],
) -> None:
    adapter, tmp_path = fake
    folder = str(_model(tmp_path, PARAKEET))

    def feed(pcm: bytes) -> dict[str, object]:
        args: dict[str, object] = {"pcm": base64.b64encode(pcm).decode()}
        return adapter.handle(Op.STREAM_FEED, args, lambda _event: None)

    adapter.handle(Op.STREAM_BEGIN, {"model": folder}, lambda _event: None)
    assert feed(_tone(2)) == {"committed": "", "tentative": ""}
    assert feed(_tone(2) + _silence(0.7)) == {"committed": "piece 1", "tentative": ""}
    feed(_tone(1))
    finished = adapter.handle(Op.STREAM_FINISH, {"restart": True}, lambda _event: None)
    assert finished == {"text": "piece 1 piece 2"}
    assert feed(_silence(0.1)) == {"committed": "", "tentative": ""}


def test_a_missing_server_binary_cannot_be_loaded(tmp_path: Path) -> None:
    with pytest.raises(EngineError) as caught:
        create(tmp_path)
    assert caught.value.kind is ErrorKind.ENGINE


def test_probe_reports_the_installed_version_without_starting_a_server(
    fake: tuple[SpeechSwift, Path],
) -> None:
    adapter, tmp_path = fake
    result = adapter.handle(Op.PROBE, {}, lambda _event: None)
    assert result["version"] == "0.0.29"
    assert result["devices"] == [{"id": "apple-silicon", "kind": "metal", "name": "Apple Silicon"}]
    assert _requests(tmp_path) == []


def test_a_pause_after_speech_ends_a_piece() -> None:
    pcm = _tone(3.5) + _silence(0.7)
    assert pause_cut(pcm) == len(pcm)


def test_short_or_continuous_speech_keeps_listening() -> None:
    assert pause_cut(_tone(1) + _silence(1)) is None
    assert pause_cut(_tone(10)) is None


def test_long_speech_is_cut_at_its_quietest_moment() -> None:
    before = _tone(12)
    pcm = before + _silence(0.2) + _tone(14)
    cut = pause_cut(pcm)
    assert cut is not None
    assert len(before) <= cut <= len(before) + len(_silence(0.2))
    assert cut % 2 == 0


@pytest.mark.parametrize(
    ("left", "right", "joined"),
    [
        ("hello", "world", "hello world"),
        ("你好", "世界", "你好世界"),
        ("", " text ", "text"),
        ("こんにちは。", "Kikoe", "こんにちは。Kikoe"),
    ],
)
def test_pieces_join_with_spaces_only_where_the_script_uses_them(
    left: str, right: str, joined: str
) -> None:
    assert join_text(left, right) == joined


def test_hub_routes_cover_tree_listings_and_file_downloads() -> None:
    assert hub_route("/api/models/org/Repo/tree/main") == ("tree", "org/Repo", "")
    assert hub_route("/org/Repo/resolve/main/a/b.json") == ("resolve", "org/Repo", "a/b.json")
    assert hub_route("/health") is None


def test_the_hub_never_serves_files_outside_the_model(tmp_path: Path) -> None:
    folder = _model(tmp_path, PARAKEET)
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    hub = LocalHub()
    hub.serve(folder)
    try:
        assert hub.file(PARAKEET, "config.json") is not None
        assert hub.file(PARAKEET, "../../../../../../secret.txt") is None
        assert hub.listing("aufklarer/../..") is None
        assert hub.listing("other/Repo") is None
    finally:
        hub.close()


def test_every_catalog_model_has_a_server_name_and_its_languages() -> None:
    models = load_catalog().models_of("speech-swift")
    assert len(models) == len(SERVER_MODELS)
    languages = catalog_languages()
    assert set(languages) == set(SERVER_MODELS)
    for model in models:
        assert model.folder
        assert not model.timestamps and model.streaming
        repos = {"/".join(f.target.split("/")[2:4]) for f in model.files}
        (repo,) = repos
        assert languages[repo] == model.languages


def test_multipart_bodies_carry_fields_and_the_file() -> None:
    body = multipart({"model": "m"}, b"RIFF", "b0undary")
    assert body.startswith(b'--b0undary\r\nContent-Disposition: form-data; name="model"\r\n\r\nm')
    assert b'name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\nRIFF' in body
    assert body.endswith(b"\r\n--b0undary--\r\n")


def test_the_engine_is_only_offered_on_apple_silicon() -> None:
    engine = load_catalog().engine("speech-swift")
    assert engine is not None
    assert [(b.platform.os.value, b.platform.arch.value) for b in engine.builds] == [
        ("macos", "arm64")
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executables")
def test_closing_stops_the_server(fake: tuple[SpeechSwift, Path]) -> None:
    adapter, tmp_path = fake
    args: dict[str, object] = {
        "model": str(_model(tmp_path, PARAKEET)),
        "audio": str(_wav(tmp_path)),
    }
    adapter.handle(Op.TRANSCRIBE, args, lambda _event: None)
    pid = int(str(_requests(tmp_path)[0]["pid"]))
    adapter.close()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
