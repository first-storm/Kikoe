"""Display-aware fixtures for GTK widget tests."""

from typing import TYPE_CHECKING

import gi
import pytest

if TYPE_CHECKING:
    from kikoe.ui.note_view import NoteView


@pytest.fixture
def view() -> NoteView:
    """Create a fresh view only when GTK has a usable display."""
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gtk

    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        pytest.skip("A display is required for GTK views")
    Adw.init()

    from kikoe.ui.note_view import NoteView

    return NoteView()
