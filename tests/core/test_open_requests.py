"""Failed and outdated reads must not block or replace the current selection."""

from kikoe.core.open_requests import OpenRequests


def test_a_reload_of_the_open_note_is_shown_when_nothing_else_was_requested() -> None:
    requests = OpenRequests()
    request = requests.start("a", reload=True)
    assert request is not None
    assert requests.finish(request, open_id="a")


def test_a_reload_started_before_another_note_was_shown_is_dropped() -> None:
    requests = OpenRequests()
    request = requests.start("b", reload=False)
    assert request is not None
    assert requests.start("a", reload=True) is None
    assert requests.finish(request, open_id="a")
    reload = requests.start("a", reload=True)
    assert reload is not None
    assert not requests.finish(reload, open_id="b")


def test_a_reload_is_dropped_when_the_user_asks_for_another_note_meanwhile() -> None:
    requests = OpenRequests()
    reload = requests.start("a", reload=True)
    assert reload is not None
    assert requests.start("b", reload=False) is not None
    assert not requests.finish(reload, open_id="a")


def test_an_older_user_request_is_dropped_even_after_the_newer_one_finishes() -> None:
    requests = OpenRequests()
    first = requests.start("a", reload=False)
    second = requests.start("b", reload=False)
    assert first is not None and second is not None
    assert not requests.finish(first, open_id=None)
    assert requests.finish(second, open_id=None)
    assert not requests.finish(first, open_id="b")


def test_failed_user_read_allows_current_note_to_reload() -> None:
    requests = OpenRequests()
    failed = requests.start("missing", reload=False)
    assert failed is not None
    requests.cancel(failed)
    reload = requests.start("open", reload=True)
    assert reload is not None
    assert requests.finish(reload, open_id="open")


def test_failed_old_read_does_not_release_a_newer_request_for_the_same_note() -> None:
    requests = OpenRequests()
    first = requests.start("a", reload=False)
    second = requests.start("a", reload=False)
    assert first is not None and second is not None
    requests.cancel(first)
    assert requests.start("open", reload=True) is None
    assert requests.finish(second, open_id="open")


def test_failed_reload_does_not_release_a_user_requested_read() -> None:
    requests = OpenRequests()
    request = requests.start("a", reload=False)
    reload = requests.start("a", reload=True)
    assert request is not None and reload is not None
    requests.cancel(reload)
    assert requests.start("open", reload=True) is None
    assert requests.finish(request, open_id="open")
