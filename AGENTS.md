# AGENTS.md

Rules for anyone changing this repository: humans, Claude, Codex, Copilot, etc.
Follow them exactly. If a rule blocks you, say so in your summary; do not quietly break it.

## Project

Kikoe is a desktop app that records or imports audio, transcribes it with cloud
speech-to-text services, and keeps the results as notes. Python 3.14, GTK 4,
libadwaita, Blueprint UI files, ffmpeg. Linux first; macOS and Windows must stay possible.

- UI spec: [docs/design.md](docs/design.md). Build screens to match it.
- Architecture: [docs/architecture.md](docs/architecture.md).
- Packaging constraints: [docs/packaging.md](docs/packaging.md).
- App ID: `io.github.first_storm.Kikoe` (defined once in `kikoe/constants.py`).

## Commands

Always use `uv run`; never `pip install` or call the system Python.

```sh
uv sync                              # install/refresh .venv
uv run scripts/build_ui.py           # compile .blp → .ui (after any .blp change)
uv run kikoe                         # run the app (KIKOE_LOG_LEVEL=DEBUG for logs)
uv run ruff format                   # format Python
uv run ruff check --fix              # lint Python
uv run scripts/build_ui.py --fix     # format Blueprint
uv run pyright                       # strict type check
uv run pytest                        # tests
uv run scripts/flatpak_deps.py       # refresh the Flatpak module after uv.lock changes
```

**Before you finish any change**, all of these must pass:

```sh
uv run ruff format --check && uv run ruff check && uv run scripts/build_ui.py --check \
  && uv run scripts/build_ui.py && uv run pyright && uv run pytest
```

If you changed UI, also launch the app and look at the result.

## Layout

```
src/kikoe/
  main.py, application.py     entry point; Adw.Application and app.* actions
  constants.py, paths.py, i18n.py
  core/        domain dataclasses and pure logic       (no gi, no I/O)
  providers/   cloud transcription clients             (no gi; async)
  media/       ffmpeg gateway, microphone capture      (no gi)
  storage/     SQLite library, settings, audio files   (no gi)
  services/    workflows across providers/media/storage (no gi; e.g. import audio)
  ui/          widgets: one foo.py + foo.blp per widget, style.css, icons/
scripts/       dev tools (build_ui.py, i18n.py, flatpak_deps.py)
packaging/     Flatpak, AppImage, macOS, Windows and ffmpeg build files (see docs/packaging.md)
tests/         pytest, mirrors src/kikoe names: test_<module>.py
data/          desktop file, metainfo (installed by packagers)
po/            translations
```

## Layers (hard rule)

- Only `kikoe/ui/`, `kikoe/application.py` and `kikoe/main.py` may import `gi`.
  Ruff enforces this (`TID251`). Do not add `noqa` to get around it.
- Dependencies point downward: `ui → services → providers/media/storage → core`. Lower
  layers never import `kikoe.ui` or `kikoe.services`; providers, media and storage do not
  import each other. A flow that needs several of them goes in `services`.
  `core` imports only the stdlib and other `core` modules.
- Business logic (formatting a transcript, merging speakers, deciding which options a
  model allows) goes in `core` with tests, not in widget code. Widgets only bind data
  to the screen and forward user intent.

## Python style

- Target Python 3.14. Use modern syntax: `X | None`, `list[int]`, `match`, `type` aliases,
  PEP 695 generics. Do **not** add `from __future__ import annotations` (3.14 already
  defers annotations).
- Fully typed. Pyright strict must pass with zero errors. No `Any` unless an external
  API forces it; no `# type: ignore` without a reason comment on the same line.
- Data: `@dataclass(frozen=True, slots=True)`; update with `dataclasses.replace`.
  Enumerations: `enum.StrEnum`. Interfaces: `typing.Protocol`, not ABCs.
- Absolute imports only (`from kikoe.core.models import Note`), enforced by ruff.
- Naming: modules `snake_case.py`; classes `PascalCase`; private helpers `_leading`.
  Avoid abbreviations except well-known ones (`url`, `id`, `ui`).
- Docstrings: one-line summary for every public module, class and non-obvious function.
  Explain *why*, not what. No commented-out code.
- Errors: raise specific exception classes defined in the module that owns the failure
  (e.g. `FFmpegError`). Messages must make sense to an end user. Never bare `except:`;
  never swallow exceptions silently.
- Logging: `log = logging.getLogger(__name__)` per module; `%`-style args
  (`log.info("Saved %s", path)`), never f-strings in log calls. No `print()` in `src/`.
- Paths: `pathlib.Path` only. User directories only via `kikoe.paths`.
- Time: timezone-aware `datetime` (`datetime.now(UTC)`); durations as `timedelta`.
- Keep functions short and flat; prefer early returns.

## Async and threading

- The GLib main loop runs an asyncio loop (PyGObject ≥ 3.58). All I/O is `async`.
- Start background work from the UI with `app.create_asyncio_task(coro)` and keep the
  returned task if it may need cancelling.
- Never block the main thread (no `time.sleep`, synchronous HTTP, or large file reads in
  handlers). Wrap unavoidable blocking calls in `await asyncio.to_thread(...)`.
- Touch GTK objects only from the main thread.

## GTK / libadwaita conventions

- Every widget subclass lives in `ui/<name>.py` with its layout in `ui/<name>.blp`.
  Load it with `@Gtk.Template(filename=ui_file("<name>"))`.
- `__gtype_name__ = "Kikoe<ClassName>"`; Blueprint uses `template $Kikoe<ClassName>`.
- Template children: `name: Type = Gtk.Template.Child()` with the annotation.
- Signal handlers: `_on_<object>_<signal>` (e.g. `_on_stop_button_clicked`), connected in
  Blueprint with `clicked => $_on_stop_button_clicked();` and decorated with
  `@Gtk.Template.Callback()` in Python.
- Actions: kebab-case. App-wide in `application.py` as `app.<name>`; window-scoped as
  `win.<name>`. Buttons and menus trigger actions, not direct method calls.
- Layout belongs in Blueprint, behavior in Python. Don't build widget trees in Python
  unless they are dynamic (list rows from a model).
- Lists: `Gio.ListStore` + `Gtk.ListView`/`Gtk.ListBox.bind_model`, not manual row juggling.
- Use stock libadwaita widgets and style classes (`suggested-action`, `destructive-action`,
  `pill`, `dimmed`, `monospace`, `numeric`, `boxed-list`, …) before writing CSS. Never
  hardcode colors or fonts; use libadwaita CSS variables in `ui/style.css` if needed.
- Follow the GNOME HIG: title-case button labels and headings, sentence-case descriptions,
  mnemonics (`_Save`) on buttons, ellipsis (`…`) on items that open a dialog, adaptive
  layouts that work at 360 px wide.
- Dialogs: `Adw.Dialog`/`Adw.AlertDialog`/`Adw.PreferencesDialog`, presented with
  `.present(parent)`. Notifications: `Adw.Toast`.
- Never edit generated `.ui` files; they are overwritten and not committed.

## Strings and translation

- Source language is English. Every user-visible string is translatable: `_()` from
  `kikoe.i18n` in Python, `_("...")` in Blueprint. Counts use `ngettext`.
- Use `"…"` (U+2026) and curly quotes `“ ”` in UI text.
- Never concatenate translated fragments; use one string with named placeholders:
  `_("Re-transcribe “{name}”").format(name=name)`.
- Log messages and exception messages for developers are not translated; messages shown
  to users are.

## Cross-platform

- No Linux-only APIs (D-Bus, GSettings, libsecret, PulseAudio, `/proc`, XDG paths by hand)
  outside a module that is explicitly platform-guarded and has a fallback.
- ffmpeg is called only through `kikoe.media.ffmpeg`. Subprocesses use argument lists,
  never `shell=True`.
- Secrets (API keys) go to the OS keyring, never to settings files or logs.
- Do not rely on case-sensitive file names or on `/` in paths.

## Tests

- Every `core`, `providers`, `media`, `storage`, `services` change comes with pytest tests.
  Tests use `tmp_path` and `monkeypatch`; no network, no real API keys, no real microphone.
- Provider clients are tested against recorded/fake HTTP responses.
- UI code is verified by running the app; pure logic must not hide in widgets so that
  it can be tested.
- Test names describe behavior: `test_subtitle_export_always_includes_timestamps`.

## Dependencies

- Add a dependency only when the feature that needs it lands, and only if the stdlib or
  an existing dependency can't do it reasonably. It must work on Linux, macOS and Windows.
- Add with `uv add <pkg>` (dev tools: `uv add --dev`); commit `uv.lock` and the
  regenerated `packaging/flatpak/python3-dependencies.json`.
- Planned choices are listed in docs/architecture.md; use those instead of alternatives.

## Git

- Conventional Commits: `feat: …`, `fix: …`, `refactor: …`, `docs: …`, `test: …`,
  `build: …`, `chore: …`. Imperative, ≤ 72-char subject.
- One logical change per commit. Don't commit generated files (`*.ui`, `*.mo`), `.venv`,
  or secrets.

## When unsure

- Prefer the simplest thing that fits these rules; this is a personal project.
- Ask before: adding dependencies, changing the app ID, changing the layer rules,
  or deviating from docs/design.md.
- Update the docs in the same change when you alter behavior they describe.
