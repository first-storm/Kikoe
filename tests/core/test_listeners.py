"""Listeners hear every change, even when one of them disconnects while being called."""

from kikoe.core.listeners import Listeners


def test_emit_calls_every_listener_in_connection_order_with_the_arguments() -> None:
    listeners = Listeners[str, int]()
    calls: list[tuple[str, str, int]] = []
    listeners.connect(lambda name, count: calls.append(("first", name, count)))
    listeners.connect(lambda name, count: calls.append(("second", name, count)))
    listeners.emit("note", 2)
    assert calls == [("first", "note", 2), ("second", "note", 2)]


def test_a_disconnected_listener_is_not_called_and_disconnecting_again_is_harmless() -> None:
    listeners = Listeners[()]()
    calls: list[str] = []
    disconnect = listeners.connect(lambda: calls.append("gone"))
    listeners.connect(lambda: calls.append("kept"))
    disconnect()
    disconnect()
    listeners.emit()
    assert calls == ["kept"]


def test_a_listener_that_disconnects_itself_does_not_skip_the_next_one() -> None:
    listeners = Listeners[()]()
    calls: list[str] = []

    def once() -> None:
        calls.append("once")
        disconnect()

    disconnect = listeners.connect(once)
    listeners.connect(lambda: calls.append("next"))
    listeners.emit()
    listeners.emit()
    assert calls == ["once", "next", "next"]
