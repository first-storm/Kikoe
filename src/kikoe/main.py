"""Entry point: configure process-wide state, then hand over to the application."""

import asyncio
import importlib
import logging
import os
import sys
import tempfile
from pathlib import Path

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.events import GLibEventLoopPolicy  # noqa: E402

from kikoe import i18n  # noqa: E402 - gi versions must be pinned before any gi import
from kikoe.diagnostics import configure_logging  # noqa: E402


def main() -> int:
    """Run Kikoe and return the process exit status."""
    level = os.environ.get("KIKOE_LOG_LEVEL", "INFO").upper()
    configure_logging(level)
    i18n.setup()

    if "--self-test" in sys.argv:
        return _self_test()

    # Imported late so that every gi.repository import sees the versions pinned above.
    from kikoe.application import Application

    # Lets Application.create_asyncio_task run coroutines on the GLib main loop.
    asyncio.set_event_loop_policy(GLibEventLoopPolicy())  # pyright: ignore[reportArgumentType, reportDeprecated]  # PyGObject 3.58 still needs the policy API
    app = Application()
    return app.run(sys.argv)


def _self_test() -> int:
    """Check packaged libraries and resources without recording or accessing providers."""
    from gi.repository import Adw, Gtk

    from kikoe.constants import VERSION
    from kikoe.media.ffmpeg import find_ffmpeg, run
    from kikoe.paths import PACKAGE_DIR

    log = logging.getLogger(__name__)
    try:
        if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 8):
            raise RuntimeError("Kikoe requires libadwaita 1.8 or newer")
        if not Gtk.init_check():
            raise RuntimeError("GTK could not open the display")
        # These imports exercise native extensions, SDK data and keyring entry points.
        for module in (
            "sounddevice",
            "openai",
            "deepgram",
            "google.genai",
            "openrouter",
            "keyring",
        ):
            importlib.import_module(module)
        importlib.import_module("kikoe.application")
        for blueprint in (PACKAGE_DIR / "ui").glob("*.blp"):
            if not blueprint.with_suffix(".ui").is_file():
                raise RuntimeError(f"Missing compiled template: {blueprint.stem}")
        catalog = PACKAGE_DIR / "locale/zh_CN/LC_MESSAGES/kikoe.mo"
        if not catalog.is_file():
            raise RuntimeError("Missing Simplified Chinese catalog")
        if VERSION == "0.0.0":
            raise RuntimeError("Missing application distribution metadata")
        executable = find_ffmpeg()
        if getattr(sys, "frozen", False):
            bundle_root = Path(getattr(sys, "_MEIPASS", sys.executable))
            if not executable.is_relative_to(bundle_root):
                raise RuntimeError("The packaged ffmpeg executable is missing")
        with tempfile.TemporaryDirectory(prefix="kikoe-self-test-") as directory:
            output = Path(directory) / "test.flac"
            asyncio.run(
                run("-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.1", str(output))
            )
            if output.stat().st_size == 0:
                raise RuntimeError("ffmpeg did not produce audio")
        log.info("Packaging self-test passed")
    except Exception as error:
        log.exception("Packaging self-test failed")
        for argument in error.args:
            if isinstance(argument, int):
                log.error("Native self-test error code: %d", argument)
        return 1
    return 0
