# Kikoe

Kikoe turns speech into text notes. It records or imports audio and transcribes it with
cloud speech-to-text services (Deepgram, OpenAI, or any OpenAI-compatible API), with
optional timestamps and speaker labels, and exports to text, Markdown, SRT, or WebVTT.

Built with Python 3.14, GTK 4 and libadwaita. Linux is the primary platform; macOS and
Windows are planned (see [docs/packaging.md](docs/packaging.md)).

> Status: early skeleton. The window opens; features are not implemented yet.

## Development setup

Requirements on every platform:

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 if needed)
- GTK 4 and libadwaita with GObject introspection data
- Headers to build PyGObject: `girepository-2.0` (GLib ≥ 2.80) and `cairo`, plus `pkg-config` and a C compiler
- `ffmpeg` on `PATH` (or set `KIKOE_FFMPEG`)
- GNU gettext, only when working on translations

Platform packages:

| Platform | Command |
| --- | --- |
| openSUSE | `sudo zypper in gtk4 libadwaita-1 typelib-1_0-Adw-1 cairo-devel glib2-devel ffmpeg gettext-tools` |
| Fedora | `sudo dnf install gtk4 libadwaita cairo-devel gobject-introspection-devel ffmpeg-free gettext` |
| Debian/Ubuntu | `sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 libgirepository-2.0-dev libcairo2-dev pkg-config ffmpeg gettext` |
| macOS (Homebrew) | `brew install gtk4 libadwaita gobject-introspection pkg-config ffmpeg gettext` |
| Windows | Use the MSYS2 UCRT64 shell: `pacman -S mingw-w64-ucrt-x86_64-{gtk4,libadwaita,gobject-introspection,cairo,pkgconf,gcc,ffmpeg,gettext}` |

Then:

```sh
uv sync                        # create .venv with runtime + dev dependencies
uv run scripts/build_ui.py     # compile Blueprint (.blp) files to .ui
uv run kikoe                   # start the app
```

Re-run `scripts/build_ui.py` whenever a `.blp` file changes.
Set `KIKOE_LOG_LEVEL=DEBUG` for verbose logs.

## Checks

```sh
uv run ruff check              # lint
uv run ruff format             # format
uv run scripts/build_ui.py --check   # Blueprint formatting + lint
uv run pyright                 # type check (strict)
uv run pytest                  # tests
```

CI runs all of these on every push.

## Documentation

- [AGENTS.md](AGENTS.md): coding rules for humans and AI assistants. Read before contributing.
- [docs/architecture.md](docs/architecture.md): layers, data flow, async model.
- [docs/design.md](docs/design.md): UI specification for every screen.
- [docs/packaging.md](docs/packaging.md): distribution plan per platform.
- [po/README.md](po/README.md): translations.
