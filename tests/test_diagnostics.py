"""Diagnostics retain useful event types without secrets, text or SDK bodies."""

import logging
import sys
from pathlib import Path

import pytest

from kikoe import diagnostics


def test_formatter_removes_dynamic_values_and_exception_bodies() -> None:
    formatter = diagnostics.PrivateFormatter("%(message)s")

    def render(record: logging.LogRecord) -> str:
        return formatter.format(diagnostics.PrivateFilter().filter(record))

    error = ValueError("secret transcript https://user:key@example.com")
    record = logging.LogRecord(
        "kikoe.ui.tasks",
        logging.ERROR,
        __file__,
        1,
        "Background task failed: %s",
        (error,),
        (ValueError, error, None),
    )
    assert render(record) == "Background task failed: ValueError\nValueError"
    assert record.args == (error,)
    record = logging.LogRecord(
        "kikoe.media.ffmpeg",
        logging.DEBUG,
        __file__,
        1,
        "Running %s",
        (["secret transcript", "API key"],),
        None,
    )
    assert render(record) == "Running [redacted]"
    external = logging.LogRecord(
        "httpx", logging.WARNING, __file__, 1, "API key and audio content", (), None
    )
    assert render(external) == "External library event (details omitted)"


def test_log_rotation_is_bounded_and_uncaught_errors_are_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.INFO)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    diagnostics.configure_logging()
    try:
        handler = next(h for h in root.handlers if isinstance(h, diagnostics.RotatingFileHandler))
        handler.maxBytes = 150
        for number in range(25):
            logging.getLogger("kikoe.tests").info("Saved %d items to %s", number, "secret path")
        sys.excepthook(ValueError, ValueError("secret API key"), None)
        files = list(tmp_path.glob("kikoe.log*"))
        assert len(files) <= 4
        text = "".join(p.read_text() for p in files)
        assert "secret" not in text
        assert "Uncaught exception" in text and "ValueError" in text
    finally:
        for handler in root.handlers:
            handler.close()


def test_unwritable_log_directory_preserves_terminal_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.INFO)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)

    def unavailable() -> Path:
        raise PermissionError("Cannot write")

    monkeypatch.setattr(diagnostics, "log_dir", unavailable)
    diagnostics.configure_logging()
    assert len(root.handlers) == 1
    assert type(root.handlers[0]) is logging.StreamHandler


def test_rotation_failure_does_not_expose_original_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.INFO)
    monkeypatch.setattr(logging, "raiseExceptions", True)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(diagnostics, "log_dir", lambda: tmp_path)
    diagnostics.configure_logging()
    handler = next(h for h in root.handlers if isinstance(h, diagnostics.RotatingFileHandler))
    handler.maxBytes = 1

    def cannot_rotate() -> None:
        raise PermissionError("Cannot rotate")

    monkeypatch.setattr(handler, "doRollover", cannot_rotate)
    error = ValueError("PRIVATE_KEY_AND_TRANSCRIPT")
    try:
        logging.getLogger("kikoe.tests").warning("Provider failed: %s", error)
        captured = capsys.readouterr()
        assert "PRIVATE_KEY_AND_TRANSCRIPT" not in captured.err
        assert "ValueError" in captured.err
    finally:
        for output in root.handlers:
            output.close()
