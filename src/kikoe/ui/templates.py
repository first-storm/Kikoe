"""Locate compiled GtkBuilder files for ``Gtk.Template``.

Each widget ``foo.py`` has a ``foo.blp`` next to it. ``scripts/build_ui.py`` compiles
it to ``foo.ui``, which is what GTK loads. Generated ``.ui`` files are not committed.
"""

from pathlib import Path

from kikoe.i18n import translated_ui

UI_DIR = Path(__file__).resolve().parent


def ui_file(name: str) -> str:
    """Return the path of the compiled ``<name>.ui`` as a string for ``Gtk.Template``."""
    path = UI_DIR / f"{name}.ui"
    if not path.is_file():
        msg = f"{path.name} not found. Compile the Blueprints first: uv run scripts/build_ui.py"
        raise FileNotFoundError(msg)
    return str(translated_ui(path))
