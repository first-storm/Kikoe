from kikoe.core.open_requests import OpenRequests


def test_a_reload_of_the_open_note_is_shown_when_nothing_else_was_requested() -> None:
    requests = OpenRequests()
    assert requests.start("a", reload=True)
    assert requests.finish("a", reload=True, open_id="a")


def test_a_reload_started_before_another_note_was_shown_is_dropped() -> None:
    requests = OpenRequests()
    assert requests.start("b", reload=False)
    assert requests.start("a", reload=True) is False
    assert requests.finish("b", reload=False, open_id="a")
    assert requests.finish("a", reload=True, open_id="b") is False


def test_a_reload_is_dropped_when_the_user_asks_for_another_note_meanwhile() -> None:
    requests = OpenRequests()
    assert requests.start("a", reload=True)
    assert requests.start("b", reload=False)
    assert requests.finish("a", reload=True, open_id="a") is False


def test_an_older_user_request_is_dropped_by_a_newer_one() -> None:
    requests = OpenRequests()
    assert requests.start("a", reload=False)
    assert requests.start("b", reload=False)
    assert requests.finish("a", reload=False, open_id=None) is False
    assert requests.finish("b", reload=False, open_id=None)
