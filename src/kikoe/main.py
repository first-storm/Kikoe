"""Entry point: configure process-wide state, then hand over to the application."""

import asyncio
import logging
import os
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.events import GLibEventLoopPolicy  # noqa: E402

from kikoe import i18n  # noqa: E402 - gi versions must be pinned before any gi import


def main() -> int:
    """Run Kikoe and return the process exit status."""
    level = os.environ.get("KIKOE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    i18n.setup()

    # Imported late so that every gi.repository import sees the versions pinned above.
    from kikoe.application import Application

    # Lets Application.create_asyncio_task run coroutines on the GLib main loop.
    asyncio.set_event_loop_policy(GLibEventLoopPolicy())  # pyright: ignore[reportArgumentType, reportDeprecated]  # PyGObject 3.58 still needs the policy API
    app = Application()
    return app.run(sys.argv)
