"""Bounded, local diagnostics with sensitive arguments removed before formatting."""

import copy
import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType
from typing import override

from kikoe.paths import log_dir


class PrivateFormatter(logging.Formatter):
    """Format filtered events without retaining exception bodies or source text."""

    @override
    def formatException(
        self,
        ei: tuple[type[BaseException], BaseException, TracebackType | None]
        | tuple[None, None, None],
    ) -> str:
        """Line numbers help diagnosis without retaining exception messages or locals."""
        exception_type, _, trace = ei
        locations = [
            f"  {Path(frame.filename).name}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(trace)
        ]
        return "\n".join([exception_type.__name__ if exception_type else "Exception", *locations])


class PrivateFilter(logging.Filter):
    """Redact before emission so logging's own write-failure report is safe too."""

    @override
    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        return _private_record(record)


def _private_record(record: logging.LogRecord) -> logging.LogRecord:
    clean = copy.copy(record)
    clean.exc_text = None
    clean.stack_info = None
    if not clean.name.startswith("kikoe."):
        clean.msg = "External library event (details omitted)"
        clean.args = ()
    elif isinstance(clean.args, dict):
        clean.args = {key: _safe_argument(value) for key, value in clean.args.items()}
    elif clean.args:
        clean.args = tuple(_safe_argument(value) for value in clean.args)
    return clean


def _safe_argument(value: object) -> object:
    if isinstance(value, BaseException):
        return type(value).__name__
    if isinstance(value, int | float):
        return value
    return "[redacted]"


def configure_logging(level: str = "INFO") -> None:
    """Keep terminal diagnostics even when the local log directory cannot be written."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))
    formatter = PrivateFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    terminal = logging.StreamHandler()
    terminal.setFormatter(formatter)
    terminal.addFilter(PrivateFilter())
    root.addHandler(terminal)
    try:
        file_handler = RotatingFileHandler(
            log_dir() / "kikoe.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
    except OSError:
        logging.getLogger(__name__).warning(
            "Local logs are unavailable; using terminal diagnostics"
        )
    else:
        file_handler.setFormatter(formatter)
        file_handler.addFilter(PrivateFilter())
        root.addHandler(file_handler)
    for name in (
        "openai",
        "openrouter",
        "deepgram",
        "google.genai",
        "httpx",
        "httpx2",
        "websockets",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
    sys.excepthook = _uncaught_exception


def _uncaught_exception(
    exception_type: type[BaseException], error: BaseException, trace: TracebackType | None
) -> None:
    logging.getLogger(__name__).critical(
        "Uncaught exception", exc_info=(exception_type, error, trace)
    )
