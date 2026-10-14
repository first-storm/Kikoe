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
  `storage.settings.SettingsStore`. Version 2 stores `providers` plus `preferences`:
  `default_model` and `recent_model` are `[provider_id, model_id]` pairs or null;
  `language` is a common BCP 47 tag or null for automatic detection. Version-one files
  retain their providers and gain empty defaults. An optional `window` object (`width`, `height`, `maximized`; core `WindowState`)
  restores the main window's size; unusable values are ignored, not reported. The frozen `Settings` and core
  `Preferences` types describe one snapshot. `storage.settings_writer.SettingsWriter` holds the
  snapshot and is the only code that writes the file: it serializes read-modify-write cycles
  with an asyncio lock, merges each field change against the latest snapshot, and never
  publishes state after a failed write. Mutations require a successful settings read, so a
  failed startup cannot overwrite a damaged file. A file with a newer `version` or damaged
  content is reported to the user, not overwritten silently. Three stores share the writer and
  each has one reason to change: `storage.providers.ProviderRepository` (providers, models,
  API keys), `storage.preferences.PreferencesRepository` (defaults, devices) and
  `storage.window_state.WindowStateStore` (window size). Each notifies only its own listeners.
  GSettings is avoided because it needs compiled schemas on
  every platform.
- Secrets (API keys): the OS credential store via `keyring` (service = app ID, username =
  provider ID), wrapped by `storage.secrets.KeyringSecretStore`. Never in settings files or
  logs. If no backend is available the user gets an error; there is no plain-text fallback.
  Behavior inside Flatpak (Secret Service over D-Bus needs `--talk-name=org.freedesktop.secrets`)
  is still to be verified.
- `ProviderRepository` keeps the provider list in memory and notifies listeners after each
  persisted change. At startup the application loads providers first and then preferences, so
  listeners see the providers before the defaults that choose among them.
- The library database is split by reason to change: `storage.schema` (the migration list and
  `SCHEMA_VERSION`), `storage.audio_files` (copying and removing retained audio),
  `storage.note_rows` (reading rows into notes) and `storage.library` (queries and writes).
  `storage.errors` holds `LibraryError` and `NoteNotFoundError`.

## Audio pipeline

Input and output devices are saved in `Preferences` by name (`None` = system default).
`media.devices` lists them and resolves a name to a PortAudio index; "System Default"
prefers the `pipewire`/`pulse`/`default` ALSA aliases because PortAudio's own default is the
first hardware device, not the desktop's choice. A missing saved device falls back to that.

File and realtime microphone transcription are implemented. File import and retained
recording retry share `services.importer.transcribe_audio` for conversion, splitting
and timestamps. `services.realtime.RealtimeRecording` owns live connections.

### Model capabilities and presets

`core.models.Capabilities` holds one `ModeCapabilities` for file uploads and one for
realtime recording (`None` when the model lacks that mode). Microphone recordings use
`realtime` when present, otherwise `file`; imports, retries and re-transcription always use
`file`. Clients read only these stored flags, never model IDs, to decide what to request.

`storage/model_presets.json` is the single source of per-model knowledge. Each API has a
fallback entry, and ordered rules (exact `ids` or `prefixes`, first match wins) override
fields for known models. Every field is `on`/`off` (default, user may change) or
`unsupported` (hard limit). `core.model_catalog` parses and validates it;
`storage.model_catalog.load_catalog` reads the packaged file. `SettingsWriter` clamps
every stored model on load and `ProviderRepository` on every save, including API-type changes, so the rest of the app
trusts `ModelSpec.capabilities`. To support a new model, add a rule to the JSON and a row
to `tests/test_model_catalog.py`.

Models with the streaming capability use the OpenAI SDK's `create(stream=True)` SSE response for
finished audio files, on OpenAI and compatible providers alike. This is independent of
microphone realtime capabilities. Models whose file capabilities include diarization use
`diarized_json`. The adapter
accumulates `transcript.text.delta`, replaces completed diarized segments by ID, and requires
`transcript.text.done` before returning a successful result. Plain text uses the complete
done text; diarized output uses segment events so speaker labels and timestamps survive.
EOF without done is retryable failure; the response is closed on cancellation or error.

`FileTranscriptSnapshot` separates completed segments from temporary previews. Optional
provider callbacks flow through the shared file service, which adds chunk offsets and
speaker scopes and includes earlier completed chunks in each snapshot. Results are
ordered by original chunk index, even when requests complete out of order.
Per-provider upload settings combine with endpoint/model bounds before conversion;
`core.upload.UploadLimits` and the pure `effective_upload_limits` function describe
this intersection. Endpoint caps and formats come only from the registry's `UploadPolicy`;
a client's `upload_limits()` adds model/option-dependent bounds (Gemini's duration) and
returns no limit otherwise. Without provider settings the client's limits apply unchanged.
Encoded chunks are checked again against both limits. A bounded worker pool shares
provider slots through `services.upload_queue`, drains cancelled/failed requests before
cleanup, and publishes only the contiguous prefix of parallel streaming previews.
See [Provider upload settings](upload-settings.md) for defaults and official sources.
SQLite is updated only after every chunk succeeds. Imports and retained recording updates shield
the final commit so cancellation cannot leave saved text pointing to discarded audio.
`services.commits.finish_commit` waits through repeated cancellation until the write finishes,
then returns its result or raises its failure. Upload workers already cancelling are drained
without cancelling them again, so SDK cleanup finishes before temporary audio is removed.
The note view overlays temporary rows at the target recording, or appends them for imports,
without changing the saved note. Its virtualized renderer is shared with microphone live
text. The file transcription display coalesces text deltas every 50 ms, immediately renders completed segments,
and removes scheduled updates when a job ends.

1. **Capture**: `media.recorder.SoundDeviceRecorder` implements the async `Recorder`
   protocol using sounddevice's `RawInputStream`, without NumPy. The chosen (or default) input
   device supplies mono int16 PCM at its native sample rate; buffers are converted to
   little endian. PortAudio callbacks only copy into a bounded queue. Overflow or device
   loss is reported, and previously accepted blocks are drained. A 200 ms device buffer
   favors reliable speech capture over monitoring latency. Pause stops the device;
   resume restarts it, excluding paused time from the audio and timer.
2. **Store**: imported originals are copied unchanged. Captured PCM is incrementally
   written by `storage.captures.CaptureStore` to `paths.data_dir()/pending-recordings/`,
   with versioned JSON metadata and fsync at least every second. Metadata contains no
   credentials. On stop, ffmpeg creates a lossless FLAC, which is retained in the library
   before any upload. Capture duration is the count of saved samples, excluding pauses.
3. **Transcode** for upload (`media.transcode.to_upload`): the provider's selected
   Ogg Opus (32 kbit/s), FLAC, or WAV encoding at 16 kHz mono. Defaults are Ogg Opus,
   except OpenAI/MiMo use WAV. ffmpeg's `-progress` output gives the length, so ffprobe
   is not needed.
4. **Split at the effective upload limit**: `UploadLimits` combines file size with
   model/option-dependent duration. Per `core.provider_registry`, OpenAI, OpenRouter and
   Groq use 25 MB; Mistral uses
   500 MB and 60 minutes; Deepgram uses 2 GB; MiMo uses WAV and 7.5 MB, since its 10 MB limit applies to Base64;
   Gemini Files accepts 2 GB and Transcribe accepts 60 minutes, or 30 minutes with
   word timestamps/speakers. Per-provider settings can lower these bounds.
   The stricter size/duration budget wins. When a limit is exceeded, `media.transcode.find_silences` runs ffmpeg's
   `silencedetect` (an energy-based voice activity check, no extra dependency), and
   `core.chunking.plan_chunks` cuts in the middle of the last pause before each part's
   limit (90 % of the size budget, since the bitrate varies). Each part is converted from
   the original, checked against the bounds again, and transcribed by bounded parallel
   workers; `offset_segments` shifts its timestamps before ordered merging.
5. **Transcribe**: each backend uses an official asynchronous SDK: `openai[realtime]`
   (also for OpenAI-compatible URLs), `openrouter` STT multipart, `google-genai`
   Interactions/Live, `deepgram-sdk` Listen v1, `groq` audio transcriptions, and
   `mistralai[realtime]` for Voxtral files and live audio.
   Groq uses the SDK root URL `https://api.groq.com`; the SDK adds `/openai/v1`.
   Its Whisper models support segment and word timestamps, without diarization,
   streaming previews or realtime sessions; recordings upload after stopping.
   Mistral uses the SDK root URL `https://api.mistral.ai`. Files go through
   `audio.transcriptions.complete_async`, or `stream_async` when the model has the
   streaming capability: text deltas are previews, segment events replace them, and
   `transcription.done` is the authoritative result (EOF without it is a retryable
   failure). Speakers come from `speaker_id` and need segment times, so requesting them
   sends only the segment granularity. Word timestamps are grouped into sentences. The API
   cannot take a language together with timestamp granularities:
   `core.choice.language_dropped` decides when the language is left out, and the option
   group tells the user. Uploads stay under 500 MB and 60 minutes, the stricter of the
   limits page and the audio overview (which says three hours). The live model
   (`voxtral-mini-transcribe-realtime-*`) is live-only, text without times or speakers,
   16 kHz PCM through `audio.realtime.connect`. The service gives no flush
   acknowledgement, so `MistralSession.flush` asks for the buffered audio and ends the turn
   once no text has arrived for a short quiet period (at most eight seconds).
   Explicit language tags use their primary language code. Factory clients own their
   SDK and transport lifetimes. Requests use JSON, verbose JSON or diarized JSON according to
   the effective model/option support; provider metadata is normalized into `Segment`.
   Gemini file requests run the async SDK in a cancellable worker loop because its
   resumable-upload retry internals contain blocking waits. Cancellation waits for
   remote-file cleanup before temporary local audio is removed. SDK debug logging is
   disabled so request headers and signed URLs cannot expose credentials.
6. **Save**: imported recording and transcript are committed together; failed or cancelled
   imports remove their copied original and upload temporary files. Microphone recordings
   are first committed with `TranscriptionState.PENDING`, then updated atomically to READY
   (including an empty successful result) or FAILED. Explicit `segments=()` replaces
   text with an empty success; `segments=None` changes status without erasing text. Failed/cancelled uploads retain their
   FLAC, and retries update the same recording with the currently selected model/options.

`services.recording.RecordingSession` coordinates capture, storage and finalization.
Closing the window or quitting stops capture and saves audio without waiting for upload.
On startup an alert offers to recover unfinished journals, or leave them for later.
Recovery trims an incomplete final PCM sample, finalizes FLAC and appends to the original
note; if that note was deleted it creates a note using the journal title. It never
uploads automatically. Lookup by recording ID makes recovery idempotent if a crash
happened after the database commit but before journal cleanup. Journals are removed
only after retained audio has been synced and the database transaction committed.
Conversion or disk failures leave the recovery files intact. A damaged or newer journal
is reported without modifying it. Database migration 2 adds `transcription_state`, with
existing recordings defaulting to READY.

## Realtime recording

Native PCM is journaled before the independent sender reads it. A streaming ffmpeg
process converts bounded buffers to mono little-endian int16 at the rate the provider asks
for. Each client declares this in `realtime_profile()` (`core.transcription.RealtimeProfile`:
sample rate, how replay checkpoints are found, and the longest a connection may live), so
`services.realtime` never names a provider. Today: 24 kHz for OpenAI, 16 kHz for the rest.
The network cannot backpressure the capture writer.
The original recording remains at the device's native sample rate.

`core.transcription` models interim/final events.
The adapter handles wire events; `LiveTranscript` replaces previews and repeated
finals. OpenAI completions are ordered by committed item, Deepgram persists every
`is_final` chunk even before `speech_final`, and Gemini repeats its finite receive
iterator for each turn. Metadata omitted by an endpoint is never synthesized.
OpenAI manual commits use PCM energy, 500 ms silence and a 15-second speech limit.

Final text is atomically fsynced to a versioned `<recording id>.transcript` sidecar;
previews are never stored. The existing v1 audio journal remains compatible. Recovery
restores both text and audio with PENDING status, without connecting to a provider.
Sidecars are removed only after retained FLAC and the database transaction are durable.

Reconnect backoff is 1, 2, 4, 8 then 15 seconds; authentication/configuration failures
stop retries. Unconfirmed ranges are replayed from the local journal at no more than
1.25 times realtime. The previously shown final text remains until the replay catches
up, then its replacement is committed. OpenAI/Gemini flush boundaries establish replay
checkpoints; Deepgram final-event audio ranges do so without exposing unwanted display
timestamps. Gemini declares a nine-minute connection lifetime, after which the service
rotates the connection. A rotation must not mark
unsent audio complete merely because microphone capture has already stopped.

Pause drains the resampler and finishes the SDK connection, then waits for resume;
resume reads from the confirmed native sample offset. Deepgram CloseStream waits for
terminal Metadata, including silent recordings. Each SDK closes within a bounded
one-second wait. Stop allows at most ten seconds for finalization; close/quit cancels
network work immediately. Incomplete transcripts keep final text and original audio,
with manual file-model retry. Successful live recordings never upload the file again.

Speaker IDs are scoped to recording and recognition connection/file part. No matching
is inferred between independent sessions; the M6 matching UI provides explicit assignments.

## Planned dependencies

Added only when the feature using them lands (see AGENTS.md, "Dependencies"):

| Need | Package | Status |
| --- | --- | --- |
| SDK HTTP transports | `httpx` | added (M2); SDK requests replace hand-written HTTP |
| OpenAI file/live and compatible APIs | `openai[realtime]` | added (M5; includes `httpx2`/`websockets`) |
| Groq file STT | `groq` | official async SDK, segment/word timestamps |
| OpenRouter file STT | `openrouter` | added (M5) |
| Gemini file/live STT | `google-genai` | added (M5) |
| Deepgram file/live STT | `deepgram-sdk` | added (M5) |
| API key storage | `keyring` | added (M1) |
| Microphone capture | `sounddevice` | added (M4); PortAudio required |

## UI structure

- `ui/window.py` + `window.blp`: `Adw.NavigationSplitView` (sidebar: notes list;
  content: a `Gtk.Stack` with the welcome page, the empty state and the note view, with
  the bottom bar below). Collapses below 640 sp. The window creates the views, creates one
  controller per kind of work, registers the `win.*` actions and wires them together; it does
  not run workflows itself. Controllers are plain classes without layout (the window's
  template supplies their widgets) and reach the window only through the `ui.window_host`
  protocol:
  - `ui.note_sidebar.NoteSidebar`: list, search (`NoteSearch`), row menu and selection.
  - `ui.note_actions.NoteActions`: create, rename and delete notes and recordings.
  - `ui.segment_editor.SegmentEditor`: in-place edits, Undo and Redo over
    `services.editing.EditingSession`, which owns optimistic segment values, history and
    serial writes. Saved-note callbacks carry the note identity so old writes cannot replace
    a newly selected note. Exports and shutdown wait for pending writes.
  - `ui.export_controller.ExportController` and `ui.file_dialogs`: export, copy and save-as.
  - `ui.speaker_controller.SpeakerController`: playback samples, speaker naming and matching
    (`services.playback.Playback` owns output tasks; the device is captured when playback is
    requested).
  - `ui.transcription_controller.TranscriptionController`: import, retry and re-transcription
    through `services.file_transcription.FileTranscriptionWorkflow`, plus the preview overlay
    (`ui.file_transcription.FileTranscriptionDisplay`).
  - `ui.recording_controller.RecordingController`: Start, Pause, Resume, Stop and startup
    recovery, over `services.recording_workflow.RecordingWorkflow` and
    `services.recovery.recover_all`.
  - `ui.window_chrome`: `GeometryKeeper` (debounced size saving) and `HeaderLayout`.
- What the window may do at any moment follows from one snapshot: `core.activity.Activity`
  describes the running work and `core.activity.action_availability` maps it to the enabled
  `win.*` actions. `services.activity.ActivityTracker` owns the import, recording and
  recovery tasks and winds them down in a fixed order when the window closes (edits saved,
  playback finished, microphone stopped, import cancelled, a recovery already writing audio
  allowed to finish).
- `ui.search.NoteSearch` owns request cancellation and stale-result rejection; SearchEntry
  retains its debounce. `ui.file_transcription.FileTranscriptionDisplay` owns the target
  note, temporary snapshots and 50 ms preview refresh scheduling. Returning to the target
  note restores its latest preview; ending the job removes scheduled updates. These helpers
  own no widget layout.
- `ui/note_view.py`: a `Gtk.ListView` over a flat list of recording headers and segments
  (`ui/transcript_row.py`), so only visible rows get widgets. The list keeps widgets for
  about 200 rows and would rebuild all of them if its items were replaced, so opening a
  note reuses the items: each takes over a row of the note, leftover items stay as
  hidden spare rows, and the rows on screen are bound at once while the others follow
  over the next frames. Prepared and recycled row widgets stay in a bounded cache outside
  the list, so sidebar folding does not rebuild placeholder rows on the empty home page.
  Segment text is a text view (`ui/segment_text.py`) that reports
  its height for any width, which `Gtk.TextView` itself does not, so no row needs a
  frame callback. Segment text is edited in place; rows commit text after a pause, on
  focus loss and when recycled. Saves of only segment contents update rows in place so
  the caret survives. The editing session owns the note-wide Undo/Redo history
  (`core/edit_history.py`) and serializes saves. Which rows change is decided in
  `core.transcript_rows` (columns, in-place updates, where an overlay sits); `ui.transcript_rows`
  builds the rows and writes a streaming transcript into the list, and `ui.row_focus` moves
  the caret between rows.
- `ui/bottom_bar.py`: model button, Import Audio, Record/Append Recording, upload progress
  and the recording controls. `Adw.WrapBox` keeps controls usable at 360 px.
- `ui/transcription_dialog.py`: the bottom bar's model and options dialog; the bar
  keeps the state and the dialog reports each change back.
- `ui/recording_view.py`: the batch-transcription placeholder. Realtime headers and
  rows join the note's existing ListView; the last row is a temporary preview.
- `ui/level_meter.py`: 28 bars drawn in the inherited theme color from actual PCM RMS;
  PCM measurement and capture states live in `core.recording`.
- Custom widgets inside other templates are added from Python into an `Adw.Bin` slot,
  because Blueprint's linter cannot inspect custom types.
- `ui/preferences.py` + `preferences.blp`: `Adw.PreferencesDialog` (General, Providers).
- Future screens from [design.md](design.md) each get their own `name.py` + `name.blp`.
- Templates load compiled `.ui` files from disk (`ui/templates.py`), not GResources,
  so no extra build step is needed for packaging beyond `scripts/build_ui.py`.
- Icons ship inside the package (`ui/icons/hicolor`) and are added to the icon theme
  search path at startup, so they work on every platform.

## Speaker identities and M6 playback

Recognition labels remain in `Segment.speaker_id`, scoped to a recording/recognition
connection. `Recording.speaker_bindings` maps each label to a note-wide `Speaker` and
retains its independent `original_id`, so many labels can be matched to one person and
later separated again. `Speaker.ordinal` is a persistent one-based number for default
names and the four theme palette slots. Names are shared throughout a note. Schema
version 3 adds these assignments and backfills older libraries without changing text.
`core.speakers` resolves identities for the transcript and all export formats.

The first recording establishes people. Later recordings containing independent labels
require explicit matching when the note already contains speakers. Applying “Keep as
New Speaker” counts as a confirmed decision; cancelling a matching dialog writes nothing.
Named old identities remain available as candidates after their original transcript is
replaced. Re-transcription uses retained audio and commits the replacement, model metadata
and bindings together only after every upload part succeeds. Failure/cancellation leaves
the previous recording, including its transcription state, intact. The final save is shielded
from cancellation: Cancel is disabled during it, and shutdown waits for its transaction.
The existing failed recording retry continues to use its pending/failed states.

Identity preservation uses merged valid time intervals, not provider numbers or turn
order: at least 80% of a new label's timed speech must overlap an old person, with a
strictly better score at least twice the runner-up. Labels with missing/invalid times
are left for manual matching. Preserving an unconfirmed identity does not confirm it.

`media.player` uses the existing sounddevice `RawOutputStream` at the chosen output
device's sample rate. `media.ffmpeg.decode_audio` streams mono int16 PCM in bounded blocks,
draining decoder diagnostics concurrently. Device calls run in worker threads; cancellation
waits for an in-flight call before closing the stream. The window serializes playback and
stops it on note changes, dialog/window closure and before capture or transcription.
No extra playback dependency, external player or Linux-specific audio API is required.


## M7 search, localization and diagnostics

`Library.search_notes` searches note titles and recording segments using parameterized
SQLite `instr` expressions and a Python `casefold` SQL function. Wildcards are literal,
Unicode matching is consistent, and no schema change is required. `NoteRepository.search`
runs this off the GTK thread. The sidebar debounces input by 200 ms, cancels old tasks
and rejects outdated results; matching summaries retain the existing newest-first order.
This personal-project implementation scans text rather than maintaining an FTS index. List and search queries share summary
projection and ordering; searches load only matching summaries.

`core.choice.resolve_choice` keeps the current model, else recent, default, first, then none.
The bottom bar persists explicit model selection through the shared settings writer.
Every import, recording, retry and retranscription captures `TranscriptionOptions.language`
at request start. Default changes during a busy session apply when the controls return idle.

Python gettext translates both Python strings and marked GtkBuilder XML properties,
including translation contexts. `ui_file` resolves a content-addressed translated template
under `paths.cache_dir()/ui`; translation markers/domain are removed to prevent C gettext
from translating twice. Original compiled files remain untouched. This also works without
`locale.bindtextdomain` on Windows/macOS. Cache failure is logged and falls back to the
original template/native GTK translation. Catalogs and templates must be built before
packaging. There is no application-level interface language override.

`diagnostics.configure_logging` writes terminal diagnostics and `paths.log_dir()/kikoe.log`.
Files rotate at 2 MiB, with three backups. The formatter keeps numeric arguments and
exception types, redacts other dynamic values and omits third-party message bodies.
Tracebacks keep module filenames/line numbers/functions without source text, exception
messages or locals. Unexpected background and uncaught errors are recorded locally.
No report is uploaded; users open the log folder and choose what to attach themselves.

## Adding a provider

A new API should need one descriptor, one client and the model presets; no caller branches on
`ApiKind`. `tests/test_architecture.py` fails when a module outside the registry names an
`ApiKind` member, and the completeness tests fail when a step below is missing.

1. `core.models.ApiKind`: add the member (its value is stored in `settings.json`; never rename).
2. `core.provider_registry`: add a `ProviderDescriptor` (display name, default base URL, upload
   limits, whether a language excludes timestamps, extra upload hints, SDK logger names).
3. `providers/<name>.py`: implement `TranscriptionProvider` on top of `providers.base.HttpClient`
   (base URL, key, test transport and the default `RealtimeProfile()`), or `FileOnlyClient`
   for services without live transcription, and register the class in
   `providers.factory.CLIENTS`.
4. `storage/model_presets.json`: add the API's fallback entry and any model rules.
5. Tests: a client test against recorded or fake responses; the registry, factory and profile
   tests cover the rest. Run `uv run scripts/i18n.py update` only if a new UI string appeared.
