# Architecture

## Goals

- Linux first, macOS and Windows later without rewrites.
- UI-independent core that can be tested without a display.
- Small: one process, no services, no plugin system.

## Layers

```
            ┌──────────────────────────────────────┐
            │ kikoe.main  →  kikoe.application     │  entry point, app actions
            │ kikoe.ui  (GTK 4 + libadwaita)       │  widgets, dialogs, Blueprints
            └───────────────┬──────────────────────┘
                            │ calls (never the other way)
   ┌────────────┬───────────┴────┬───────────────┐
   │ providers  │ media          │ storage       │  I/O adapters, async
   │ (HTTP/WS)  │ (ffmpeg, mic)  │ (SQLite, files│
   └─────┬──────┴──────┬─────────┴──────┬────────┘
         └─────────────┴─ kikoe.core ───┘           pure domain types + logic
```

Rules (enforced by ruff `TID251` where possible, see `pyproject.toml`):

| Package | May import | Must not import |
| --- | --- | --- |
| `kikoe.core` | stdlib | `gi`, other kikoe packages |
| `kikoe.providers`, `kikoe.media`, `kikoe.storage` | stdlib, `kikoe.core`, `kikoe.paths`, third-party I/O libs | `gi`, `kikoe.ui` |
| `kikoe.ui` | everything above, `gi` | — |
| `kikoe.application`, `kikoe.main` | everything | — |

Top-level helpers (`constants`, `paths`, `i18n`) are importable from any layer.

## Runtime model

- **One thread, one loop.** PyGObject ≥ 3.58 runs an asyncio event loop on top of the
  GLib main loop inside `Gio.Application.run()`. Use `async def` for all I/O and start
  work from the UI with `Gio.Application.create_asyncio_task(coro)` (it keeps the app
  alive until the task finishes). Never block the main loop with synchronous network
  or disk work; use `asyncio.to_thread` for unavoidable blocking calls.
- **UI updates** happen on the main thread only. Code running in a worker thread must
  hand results back via the loop (awaiting `to_thread`) or `GLib.idle_add`.
- **Errors** from lower layers are typed exceptions (`TranscriptionError`,
  `FFmpegError`, …) with user-presentable messages. The UI catches them at the point
  where it started the task and shows an `Adw.Toast` or dialog.

## Data

- Domain objects in `kikoe.core.models` are frozen dataclasses; change them with
  `dataclasses.replace`.
- Library: SQLite at `paths.data_dir()/library.sqlite3`; retained original audio under
  `paths.data_dir()/audio/`. (Not implemented yet.)
- Settings: `paths.config_dir()/settings.json`, written atomically by
  `storage.settings.SettingsStore`. Shape: `{"version": 1, "providers": [{"id", "name",
  "api", "base_url", "models": [{"id", "name", "realtime", "timestamps",
  "diarization"}]}]}`. A file with a newer `version` or damaged content is reported to the
  user, not overwritten silently. GSettings is avoided because it needs compiled schemas on
  every platform.
- Secrets (API keys): the OS credential store via `keyring` (service = app ID, username =
  provider ID), wrapped by `storage.secrets.KeyringSecretStore`. Never in settings files or
  logs. If no backend is available the user gets an error; there is no plain-text fallback.
  Behavior inside Flatpak (Secret Service over D-Bus needs `--talk-name=org.freedesktop.secrets`)
  is still to be verified.
- `storage.providers.ProviderRepository` combines both, keeps the provider list in memory and
  notifies listeners (window, preferences) after each persisted change.

## Audio pipeline (planned)

1. **Capture**: `kikoe.media.recorder.Recorder` yields PCM chunks. Planned backend:
   `sounddevice` (PortAudio; ships binaries for macOS/Windows wheels).
2. **Store**: write the original to the data dir (e.g. FLAC/Opus via ffmpeg).
3. **Transcode** for upload with ffmpeg (`kikoe.media.ffmpeg.run`) when a provider
   needs a specific format or size.
4. **Transcribe**: realtime models stream PCM over WebSocket while recording; others
   upload the finished file. Both go through `kikoe.providers.base.TranscriptionProvider`.

## Planned dependencies

Added only when the feature using them lands (see AGENTS.md, "Dependencies"):

| Need | Package |
| --- | --- |
| HTTP for batch transcription | `httpx` |
| Realtime streaming | `websockets` |
| API key storage | `keyring` |
| Microphone capture | `sounddevice` |

## UI structure

- `ui/window.py` + `window.blp`: `Adw.NavigationSplitView` (sidebar: notes list;
  content: a `Gtk.Stack` with the welcome page now, note view later). Collapses below
  640 sp.
- `ui/preferences.py` + `preferences.blp`: `Adw.PreferencesDialog` (General, Providers).
- Future screens from [design.md](design.md) each get their own `name.py` + `name.blp`.
- Templates load compiled `.ui` files from disk (`ui/templates.py`), not GResources,
  so no extra build step is needed for packaging beyond `scripts/build_ui.py`.
- Icons ship inside the package (`ui/icons/hicolor`) and are added to the icon theme
  search path at startup, so they work on every platform.
