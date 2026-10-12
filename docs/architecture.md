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
            ┌───────────────┴──────────────────────┐
            │ kikoe.services                       │  workflows, e.g. import audio
            └───────────────┬──────────────────────┘
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
| `kikoe.providers`, `kikoe.media`, `kikoe.storage` | stdlib, `kikoe.core`, `kikoe.paths`, third-party I/O libs | `gi`, `kikoe.ui`, `kikoe.services`, each other |
| `kikoe.services` | the rows above | `gi`, `kikoe.ui` |
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
- Library: SQLite at `paths.data_dir()/library.sqlite3` (`storage.library.Library`), with
  tables `notes`, `speakers`, `recordings` and `segments`; times are ISO 8601 UTC,
  durations milliseconds. The schema version is `PRAGMA user_version`; `_MIGRATIONS` holds
  one script per version, applied in order at startup, and released entries are never
  edited. A database from a newer Kikoe is refused, not modified. Every call opens its
  own connection, so it can run in any `asyncio.to_thread` worker.
- Retained original audio: copied to `paths.data_dir()/audio/<recording id><ext>` on
  import; the database stores the name relative to that directory. Deleting a note
  deletes its files.
- `storage.notes.NoteRepository` is the async front of the library: it keeps sidebar
  summaries in memory and tells listeners which note changed.
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

## Audio pipeline

Importing (`services.importer.import_audio`) is implemented; recording is planned.

1. **Capture** (planned): `kikoe.media.recorder.Recorder` yields PCM chunks. Planned
   backend: `sounddevice` (PortAudio; ships binaries for macOS/Windows wheels).
2. **Store**: copy the original into the library's audio directory, unchanged, so it can
   be re-transcribed or exported later.
3. **Transcode** for upload (`media.transcode.to_upload`): Ogg Opus, 16 kHz mono,
   32 kbit/s, which fits about 1¾ hours in 25 MB and is accepted by OpenAI, Groq and most
   compatible servers. ffmpeg's `-progress` output gives the length, so ffprobe is not
   needed.
4. **Split if still too large**: when the file exceeds the client's `max_upload_bytes`
   (25 MB for OpenAI-style APIs), `media.transcode.find_silences` runs ffmpeg's
   `silencedetect` (an energy-based voice activity check, no extra dependency), and
   `core.chunking.plan_chunks` cuts in the middle of the last pause before each part's
   limit (90 % of the size budget, since the bitrate varies). Each part is converted from
   the original and transcribed in turn; `offset_segments` shifts its timestamps.
5. **Transcribe**: realtime models (planned) stream PCM over WebSocket while recording;
   others upload the finished file. Both go through
   `kikoe.providers.base.TranscriptionProvider`; `providers.factory.create_client` picks
   the client for a provider's API type. `providers.openai.OpenAIClient` asks for
   `verbose_json` segments only when the model has the timestamps capability.
6. **Save**: the recording and its segments are written in one transaction. If any step
   fails or is cancelled, the copied original and temporary files are removed.

## Planned dependencies

Added only when the feature using them lands (see AGENTS.md, "Dependencies"):

| Need | Package | Status |
| --- | --- | --- |
| HTTP for batch transcription | `httpx` | added (M2) |
| Realtime streaming | `websockets` | planned |
| API key storage | `keyring` | added (M1) |
| Microphone capture | `sounddevice` | planned |

## UI structure

- `ui/window.py` + `window.blp`: `Adw.NavigationSplitView` (sidebar: notes list;
  content: a `Gtk.Stack` with the welcome page, the empty state and the note view, with
  the bottom bar below). Collapses below 640 sp.
- `ui/note_view.py`: a `Gtk.ListView` over a flat list of recording headers and segments
  (`ui/transcript_row.py`), so only visible rows get widgets.
- `ui/bottom_bar.py`: model picker, Import Audio and import progress.
- Custom widgets inside other templates are added from Python into an `Adw.Bin` slot,
  because Blueprint's linter cannot inspect custom types.
- `ui/preferences.py` + `preferences.blp`: `Adw.PreferencesDialog` (General, Providers).
- Future screens from [design.md](design.md) each get their own `name.py` + `name.blp`.
- Templates load compiled `.ui` files from disk (`ui/templates.py`), not GResources,
  so no extra build step is needed for packaging beyond `scripts/build_ui.py`.
- Icons ship inside the package (`ui/icons/hicolor`) and are added to the icon theme
  search path at startup, so they work on every platform.
