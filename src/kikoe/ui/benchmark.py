"""Measure the real sidebar-to-painted-frame path without preopening test notes."""

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kikoe.application import Application
    from kikoe.ui.window import Window


@dataclass(frozen=True, slots=True)
class Measurement:
    """One real sidebar selection, including asynchronous I/O, layout and painting."""

    title: str
    first_open: bool
    milliseconds: float


async def select_and_paint(window: Window, note_id: str, sidebar_position: int) -> float:
    """Wait for the selected note's complete logical model and an allocated frame."""
    future: asyncio.Future[float] = asyncio.get_running_loop().create_future()
    clock = window.get_frame_clock()
    if clock is None:
        raise RuntimeError("The window has no frame clock")
    started = time.perf_counter()

    def painted(_clock: object) -> None:
        note = window.note
        if note is None or note.note_id != note_id or future.done():
            return
        model = window.note_view.transcript_list.get_model()
        count = sum(1 + len(r.segments) for r in note.recordings)
        from gi.repository import Gtk

        if (
            isinstance(model, Gtk.NoSelection)
            and model.get_n_items() == count
            and window.note_view.layout_ready()
        ):
            future.set_result((time.perf_counter() - started) * 1000)

    handler = clock.connect("after-paint", painted)
    try:
        row = window.notes_list.get_row_at_index(sidebar_position)
        if row is None:
            raise RuntimeError("The test note is absent from the sidebar")
        window.notes_list.select_row(row)
        window.queue_draw()
        async with asyncio.timeout(30):
            return await future
    finally:
        clock.disconnect(handler)


async def benchmark(
    app: Application,
    prefix: str,
    repeats: int,
    measurements: list[Measurement],
    on_measurement: Callable[[Measurement], None] | None,
) -> None:
    from kikoe.ui.window import Window

    loaded = asyncio.Event()
    disconnect = app.notes.connect_changed(lambda _note_id: loaded.set())
    try:
        if not app.notes.notes:
            async with asyncio.timeout(30):
                await loaded.wait()
    finally:
        disconnect()
    window = app.get_active_window()
    if not isinstance(window, Window):
        raise RuntimeError("The application did not open its main window")
    # Stabilize startup; do not open or render any note before its first measurement.
    await asyncio.sleep(0.3)
    tests = sorted(
        ((position, n) for position, n in enumerate(app.notes.notes) if n.title.startswith(prefix)),
        key=lambda pair: pair[1].title,
    )
    if len(tests) < 2:
        raise RuntimeError("At least two matching notes are needed to measure actual switching")
    for repeat in range(repeats + 1):
        for position, summary in tests:
            milliseconds = await select_and_paint(window, summary.note_id, position)
            measurement = Measurement(summary.title, repeat == 0, milliseconds)
            measurements.append(measurement)
            if on_measurement is not None:
                on_measurement(measurement)
            await asyncio.sleep(0.1)


def run(
    prefix: str, repeats: int, on_measurement: Callable[[Measurement], None] | None = None
) -> list[Measurement]:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.events import GLibEventLoopPolicy
    from gi.repository import GLib

    from kikoe import i18n

    i18n.setup()
    from kikoe.application import Application

    asyncio.set_event_loop_policy(GLibEventLoopPolicy())  # pyright: ignore[reportArgumentType, reportDeprecated]  # PyGObject 3.58 still needs the policy API
    app = Application()
    measurements: list[Measurement] = []
    failures: list[Exception] = []

    async def execute() -> None:
        try:
            await benchmark(app, prefix, repeats, measurements, on_measurement)
        except Exception as error:
            failures.append(error)
        finally:
            app.quit()

    def start() -> bool:
        from kikoe.ui.tasks import run_task

        window = app.get_active_window()
        if window is None:
            failures.append(RuntimeError("The application did not open its main window"))
            app.quit()
        else:
            run_task(execute(), window)
        return False

    GLib.timeout_add(100, start)
    app.run(["kikoe-note-benchmark"])
    if failures:
        raise failures[0]
    return measurements
