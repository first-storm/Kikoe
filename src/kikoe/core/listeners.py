"""Change notifications that repositories send to the screens showing their data."""

from collections.abc import Callable


class Listeners[*Ts]:
    """Callbacks called in connection order; any of them may disconnect while being called."""

    def __init__(self) -> None:
        self._callbacks: list[Callable[[*Ts], None]] = []

    def connect(self, callback: Callable[[*Ts], None]) -> Callable[[], None]:
        """Call ``callback`` on every emit; returns a function that stops the calls."""
        self._callbacks.append(callback)
        return lambda: self._disconnect(callback)

    def emit(self, *args: *Ts) -> None:
        # A copy, so that a callback removing itself does not make the next one be skipped.
        for callback in tuple(self._callbacks):
            callback(*args)

    def _disconnect(self, callback: Callable[[*Ts], None]) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)
