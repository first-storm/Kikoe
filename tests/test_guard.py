import pytest

from kikoe.ui.guard import Guard


def test_guard_is_active_only_inside_its_block() -> None:
    guard = Guard()
    assert not guard.active
    with guard:
        assert guard.active
    assert not guard.active


def test_nested_guard_stays_active_until_the_outer_block_ends() -> None:
    guard = Guard()
    with guard:
        with guard:
            assert guard.active
        assert guard.active
    assert not guard.active


def test_guard_is_released_when_the_block_raises() -> None:
    guard = Guard()
    with pytest.raises(ValueError), guard:
        raise ValueError
    assert not guard.active


def test_guard_can_start_held() -> None:
    assert Guard(1).active
