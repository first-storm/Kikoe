from kikoe.core.decoration_layout import DecorationLayout


def test_macos_layout_puts_controls_at_start() -> None:
    layout = DecorationLayout.parse("close,minimize,maximize:")
    assert layout.controls_at_start
    assert layout.close_only() == "close:"


def test_gnome_default_keeps_controls_at_end() -> None:
    layout = DecorationLayout.parse("appmenu:close")
    assert not layout.controls_at_start
    assert layout.close_only() == ":close"


def test_split_layout_is_not_treated_as_start_heavy() -> None:
    layout = DecorationLayout.parse("close:maximize")
    assert not layout.controls_at_start
    assert layout.close_only() == "close:"


def test_layout_without_colon_is_all_at_start() -> None:
    layout = DecorationLayout.parse("close,minimize")
    assert layout.start == ("close", "minimize")
    assert layout.end == ()
    assert layout.controls_at_start


def test_icon_and_menu_are_not_window_controls() -> None:
    layout = DecorationLayout.parse("icon,menu:minimize,close")
    assert not layout.controls_at_start


def test_blank_entries_are_ignored() -> None:
    layout = DecorationLayout.parse(" close , ,minimize : ")
    assert layout.start == ("close", "minimize")
    assert layout.end == ()


def test_empty_layout_has_no_controls() -> None:
    layout = DecorationLayout.parse("")
    assert not layout.controls_at_start
    assert layout.close_only() == ":"
