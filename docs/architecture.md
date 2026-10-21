# Architecture

Kikoe is a desktop application for recording or importing audio, transcribing it
with cloud services or local models, and organizing the results as notes. It runs in one process,
with a GTK 4 and libadwaita interface and a Python core that can be tested without
a display. Platform-specific details stay behind adapters so Linux, macOS and
Windows can share the same workflows.

This document explains the boundaries between modules, the lifecycle of audio and
transcripts, and the extension points for providers. See [UI design](design.md)
for interface behavior, [Packaging](packaging.md) for distribution requirements,
and [Provider upload settings](upload-settings.md) for upload defaults and limits.
Repository contribution rules are in [AGENTS.md](../AGENTS.md).

## Module boundaries

```text
main / application / ui          GTK interface and application actions
           ↓
        services                workflows and coordination
           ↓
  providers   media   storage    network, audio and persistence adapters
           ↓
          core                  domain types and pure logic
```

Dependencies point downward. Providers, media and storage do not import one
another; a workflow that needs several adapters belongs in `services`.

| Package | Responsibility | Allowed dependencies |
| --- | --- | --- |
| `core` | Domain objects, capabilities, formatting and decisions | Standard library and other core modules |
| `providers` | Cloud transcription clients | Core, shared helpers and third-party I/O libraries |
| `media` | ffmpeg, capture, devices and playback | Core, shared helpers and third-party I/O libraries |
| `storage` | SQLite, settings, retained audio and credentials | Core, shared helpers and third-party I/O libraries |
| `services` | Workflows spanning adapters | Core, providers, media, storage and shared helpers |
| `ui` | Widgets, dialogs and user actions | All lower layers and `gi` |
| `application`, `main` | Startup and application-wide actions | All layers and `gi` |

Only `ui`, `application` and `main` may import `gi`. Ruff enforces that restriction
with `TID251`; architecture tests check additional boundaries. Top-level helpers
such as `constants`, `paths` and `i18n` are shared across layers.

## Runtime and startup

PyGObject 3.58 or newer integrates asyncio with the GLib main loop inside
`Gio.Application.run()`. UI tasks start through
`Gio.Application.create_asyncio_task(coro)`, which keeps the application alive
until the task finishes. Network and file work use asynchronous interfaces;
unavoidable blocking calls run through `asyncio.to_thread`.

GTK objects are accessed only on the main thread. Worker results return through
an awaited call or `GLib.idle_add`. Lower layers raise specific exceptions, such
as `TranscriptionError` or `FFmpegError`. The initiating UI controller presents
the failure as a dialog or toast.

Provider SDKs and `keyring` load lazily to keep startup responsive.
`providers.factory` imports a client when constructing it, and error handling and
credential storage import their libraries inside the functions that need them.
After settings load, `services.prewarm` constructs the default provider's client
(or the recently used provider's when no default is available) in a worker thread,
preparing its SDK before the first transcription. In parallel, it asynchronously
reads that provider's API key to initialize the credential store before the first
recording. The key is discarded; transcription still reads the current key.
Built-in providers skip the credential store. Prewarm failures are logged without
interrupting startup, and credential prewarm logs omit keys and error details.
Other providers load on first use. `tests/repository/test_lazy_imports.py` guards this boundary.

## Domain data and persistence

Domain objects in `core.models` are frozen dataclasses. Changes create a new
snapshot with `dataclasses.replace`.

### Note library and original audio

`storage.library.Library` stores notes, speakers, recordings and segments in
`paths.data_dir()/library.sqlite3`. Times use ISO 8601 UTC; durations use
milliseconds. Each call opens its own connection and can run in a worker thread.

The database modules have separate responsibilities:

| Module | Responsibility |
| --- | --- |
| `storage.schema` | `SCHEMA_VERSION` and ordered migrations |
| `storage.library` | Queries and writes |
| `storage.note_rows` | Converting database rows to domain objects |
| `storage.audio_files` | Copying and removing retained audio |
| `storage.errors` | `LibraryError` and `NoteNotFoundError` |
| `storage.notes` | Async repository, cached sidebar summaries and typed change notifications |

`NoteRepository` reloads summaries only after changes that can alter them
(creating, renaming or deleting notes, adding or deleting recordings) and then
emits `SummariesChanged`; the sidebar refreshes. Display options, speaker names
and matches, and transcription results emit `ContentChanged`, which reloads only
the open note. Segment edits emit nothing and are a single `UPDATE` that checks
the segment belongs to the note: the editor already shows them, and no summary
depends on segment text. An active search therefore keeps its results until the
query changes.

`PRAGMA user_version` records the schema version. Migrations run in order at
startup; released migrations are immutable. A database from a newer application
version is refused without modification. Migration 2 adds transcription states;
migration 3 adds speaker assignments and backfills existing records.

Imported originals are copied unchanged to
`paths.data_dir()/audio/<recording id><extension>`. The database stores paths
relative to that directory. Captured audio is retained as lossless FLAC. Deleting
a note removes its associated audio files.

### Settings and credentials

`storage.settings.SettingsStore` reads and atomically writes
`paths.config_dir()/settings.json`. Version 2 contains providers and preferences.
The recent model is a `[provider_id, model_id]` pair or null; the
transcription language is a BCP 47 tag or null for automatic detection. Version 1
files keep their providers and receive empty defaults. Optional window geometry
stores width, height and maximized state; invalid geometry is ignored.

`storage.settings_writer.SettingsWriter` owns the current immutable snapshot and
serializes read-modify-write operations with an asyncio lock. Each change merges
with the latest snapshot. A failed write does not publish new state, and mutation
requires a successful initial read. Damaged files and unsupported newer versions
are reported rather than overwritten.

Three repositories share this writer: `ProviderRepository` for providers, models
and keys; `PreferencesRepository` for defaults and devices; and `WindowStateStore`
for geometry. Each notifies its own listeners through `core.listeners.Listeners`,
which tolerates listeners disconnecting during notification. Startup loads
providers before preferences so defaults can resolve against an available list.
JSON settings avoid the platform-specific compiled schemas required by GSettings.

API keys use the operating system credential store through
`storage.secrets.KeyringSecretStore`. The keyring service is the application ID
and the username is the provider ID. Keys never enter settings files or logs.
There is no plain-text fallback: an unavailable backend produces an error.
Flatpak grants access to Secret Service; packaged keyring behavior still requires
platform acceptance testing.

## Models and provider capabilities

`core.models.Capabilities` contains separate `ModeCapabilities` for file and
realtime transcription. An absent mode is represented by `None`. Microphone
sessions prefer realtime when available; imports, retries and re-transcription
always require file support. Clients make requests from stored capabilities,
rather than interpreting model IDs themselves.

`storage/model_presets.json` is the source of model-specific knowledge. Each API
has a fallback, followed by ordered exact-ID or prefix rules; the first matching
rule wins. Fields are `on` or `off` for editable defaults, or `unsupported` for
hard restrictions. `core.model_catalog` validates the catalog and
`storage.model_catalog` loads it. Settings are clamped on load and whenever a
model is saved, including after an API-type change. Add a catalog rule and a test
in `tests/storage/test_model_catalog.py` when introducing a known model.

## File transcription

Imports and retained recordings share `services.importer.transcribe_audio` for
conversion, chunking and timestamp offsets.
`services.file_transcription.FileTranscriptionWorkflow` owns the API key, client
and failure policy. Its pending-recording path marks failures and cancellation
as FAILED; re-transcription preserves the previous recording until replacement
succeeds.

The pipeline has six stages:

1. **Acquire audio.** An import copies the original file. Microphone capture uses
   `media.recorder.SoundDeviceRecorder`, implementing the async `Recorder`
   protocol with sounddevice's `RawInputStream` and no NumPy dependency. It
   captures mono int16 PCM at the input device's native rate, converts buffers to
   little endian, and copies callbacks into a bounded queue. Overflow or device
   loss is reported after accepted blocks drain. A 200 ms buffer favors reliable
   capture. Pause stops the device; resume restarts it.
2. **Retain local audio.** `storage.captures.CaptureStore` incrementally writes
   PCM and versioned JSON metadata to `paths.data_dir()/pending-recordings/`,
   syncing at least once per second. Metadata contains no credentials. On stop,
   ffmpeg produces a lossless FLAC that is retained before uploading. Duration
   comes from the saved sample count and excludes pauses.
3. **Encode uploads.** `media.transcode.to_upload` produces 16 kHz mono Ogg Opus
   or MP3 at 32 kbit/s, FLAC or WAV. Most APIs default to Ogg Opus; OpenAI defaults
   to WAV; MiMo and Audio LLM, which accept only MP3 or WAV, default to MP3. Clients
   that must name the encoding read it from the chunk with `core.upload.upload_format_of`. ffmpeg progress supplies duration, so ffprobe is unnecessary.
4. **Plan and validate chunks.** `core.upload.effective_upload_limits` intersects
   user settings with endpoint and model bounds. Endpoint formats and caps belong
   to the registry's `UploadPolicy`; client `upload_limits()` supplies additional
   model or option limits, such as Gemini's duration cap. When splitting is needed,
   ffmpeg `silencedetect` locates pauses and `core.chunking.plan_chunks` prefers
   the last pause before a boundary, using 90% of the size budget to allow for
   bitrate variation. Each chunk is encoded from the original, checked again,
   and split further if necessary.
5. **Transcribe and merge.** Official async SDK clients run chunks through a
   bounded worker pool. `services.upload_queue` shares request slots between jobs
   using the same configured provider. Results merge by source index, with
   timestamp offsets and speaker scopes applied before assembly.
6. **Commit the result.** SQLite changes only after every chunk succeeds. Imported
   audio and text commit together; failed or cancelled imports remove their
   copied originals. Microphone audio is committed first as PENDING, then updated
   atomically to READY or FAILED. An empty successful transcript is READY:
   `segments=()` replaces text with an empty result, whereas `segments=None`
   updates status without erasing text. Failed uploads retain FLAC for retry.

Final writes are shielded from cancellation.
`services.commits.finish_commit` waits through repeated cancellation until the
write succeeds or fails. Workers are stopped and drained before temporary audio
is removed; workers already cancelling are not cancelled again during cleanup.

### File streaming previews

File streaming and realtime microphone transcription are independent
capabilities. OpenAI and compatible adapters use `create(stream=True)` when file
streaming is enabled. Diarization selects `diarized_json`; otherwise the adapter
uses the appropriate JSON, verbose JSON or text response.

The OpenAI adapter accumulates `transcript.text.delta`, replaces completed
diarized segments by ID, and requires `transcript.text.done` for success. Plain
text uses the completed text; diarized output uses segment events to retain
speakers and times. EOF without a completion event is a retryable failure.
Responses close on error or cancellation.

MiMo uses the chat completions endpoint with `stream=True` when file streaming
is enabled. It accumulates the first choice’s text deltas as previews and requires
`finish_reason: stop` before committing the result. Both streaming and ordinary
responses reject output stopped by length limits or content filtering; truncated
output advises smaller upload chunks rather than retrying the same request.
EOF without a successful finish reason is a retryable streaming failure.
New MiMo models enable file streaming by default; existing model settings remain
unchanged. This does not enable realtime microphone transcription.

Audio LLM providers send each WAV chunk with a prompt to a general audio-capable
model: as an `input_audio` part of a chat completion, or as inline data to Gemini
`generateContent` / `streamGenerateContent`. `core.llm_prompt` owns the built-in
prompt, the language hint added for a chosen language and cleanup of a wrapping
code fence. An empty stored prompt means the built-in one. Both formats share
`providers.chat_file_stream` with MiMo; Gemini finish reasons are mapped to the
OpenAI names (`MAX_TOKENS` → `length`, safety reasons → `content_filter`). Unlike
MiMo, an empty successful reply is a silent chunk, not an error.

`FileTranscriptSnapshot` separates completed segments from temporary previews.
Callbacks pass through the shared service, which adds chunk offsets, scopes
speakers and includes earlier completed chunks. Parallel previews expose only
the contiguous prefix of source-order results. The UI refreshes text deltas every
50 ms, renders completed segments immediately, and removes scheduled updates when
the job ends. Temporary rows overlay a retained recording or append an import
section without changing the saved note.

### Provider-specific behavior

| API | Adapter and behavior |
| --- | --- |
| OpenAI and compatible | `openai[realtime]`; file and live support follow stored model capabilities |
| OpenRouter | `openrouter`; multipart speech-to-text uploads |
| Groq | `groq`; SDK root `https://api.groq.com` adds `/openai/v1`; Whisper supports segment times, with uploads after recording stops |
| Gemini | `google-genai`; file Interactions and Live sessions |
| Deepgram | `deepgram-sdk`; Listen v1 for file and live transcription |
| Mistral | `mistralai[realtime]`; Voxtral file and live transcription, SDK root `https://api.mistral.ai` |
| MiMo | `openai`; WAV input through chat completions, with optional file streaming previews |
| Audio LLM | `openai` chat completions or `google-genai` `generateContent`, chosen by the provider's request format; WAV plus a prompt, plain text, optional file streaming previews |

Mistral files use `audio.transcriptions.complete_async`, or `stream_async` for
streaming models. Text deltas are previews; segment events replace them;
`transcription.done` supplies the authoritative result. EOF without it is a
retryable failure. Timestamp or speaker requests use segment granularity.
Language cannot be combined with timestamp granularities,
so `core.choice.language_dropped` controls omission and the UI explains automatic
detection. Primary language codes are sent for explicit language tags. The live
`voxtral-mini-transcribe-realtime-*` family accepts 16 kHz PCM and returns text
without times or speakers; Voxtral 3 (`voxtral-mini-transcribe-realtime-3-0`)
matches that prefix and so inherits the live-only rule, while
`voxtral-small-transcribe-3-0` and `voxtral-mini-transcribe-3-0` are ordinary file
models. Without a flush acknowledgement, `MistralSession.flush`
uses a quiet period, bounded at eight seconds.

Two preferences reach Mistral through `TranscriptionOptions`: `context_bias`
(custom vocabulary) is sent as `context_bias` on file requests, and
`realtime_delay_ms` becomes `target_streaming_delay_ms` (80–2400 ms) when a live
session opens. The SDK's realtime session has no vocabulary field, so live
sessions do not use the vocabulary. Other providers ignore both options.

Gemini file requests use a cancellable worker event loop because resumable-upload
retries can block. Cancellation waits for remote-file cleanup before local
cleanup. Factory clients own SDK and transport lifetimes. SDK debug logging is
disabled to keep request headers and signed URLs out of diagnostics.

See [Provider upload settings](upload-settings.md) for exact size and duration
bounds, format defaults and the official sources behind them.

## Local engines

Local models run in an engine worker process, not in the app. Everything about an
engine lives in `providers/local/engines/<engine>/`: `catalog.json` lists its
downloadable builds and models, each file pinned by URL and SHA-256, and
`adapter.py` is the only code that imports the engine's own binding.
`providers/local/engines/__init__.py` registers each engine package in one line.
Removing an engine means deleting its folder and that line; architecture tests
ensure nothing else imports engine packages, and adapters import only
`providers.local.adapter` and `providers.local.protocol`.

| Module | Responsibility |
| --- | --- |
| `core.local_models` | Catalog validation, build recommendation, install state and `ModelSpec` mapping |
| `core.diarization` | Assigning speaker turns to words or timed segments |
| `storage.downloads` | HTTPS downloads into `.part` files, size and SHA-256 checks, cancellation |
| `storage.local_installs` | Install folders and receipts under `data_dir()/local`; bundled engines |
| `storage.local_settings` | Device choices in `config_dir()/local.json`, apart from `settings.json` |
| `providers.local.protocol` | JSON-lines requests, replies and events with a version handshake |
| `providers.local.worker_host` | Starting the worker, request routing, cancellation, crash handling |
| `providers.local.worker_main` | The worker loop: a reader thread and one compute thread |
| `providers.local.connection` | Typed engine calls and user-facing error messages |
| `providers.local.client`, `.session` | `TranscriptionProvider` and `RealtimeSession` for local models |
| `services.local_models` | `LocalModelManager`: state, downloads, probes, workers and the built-in provider |

Engines and models are downloaded after installation, never at build time, except
on macOS, where `scripts/bundle_engine.py` prepares transcribe.cpp (2.4 MB) and the
PyInstaller spec ships it as `local-engines/<engine>`. A download is unpacked into
`staging/`, a receipt is written, and only then is the folder swapped into place;
leftovers are removed at startup. Archive members cannot leave their folder. A
folder without a valid receipt does not count as installed, and a model whose
pinned checksum changes needs downloading again.

The worker is the app's own interpreter: `python -m
kikoe.providers.local.worker_main <engine> <folder>` from source, or `Kikoe
--engine-worker …` in frozen builds, recognised by `packaging/pyinstaller/launch.py`
before GTK loads. It starts on the first request and exits when its stdin closes,
so it never outlives the app. Native output is moved off the protocol stream to the
log. A cancel aborts the running native call; a worker that does not answer within
five seconds is killed. A crashed worker fails its requests as retryable errors and
the next request starts a new one. Replacing or removing an engine stops its
worker first.

transcribe.cpp's binding loads the downloaded native library through
`TRANSCRIBE_LIBRARY`. Files are converted to 16 kHz mono WAV by the existing upload
pipeline and passed by path; models with a `max_seconds` limit are split at pauses
like cloud uploads. Live audio is sent as Base64 PCM: previews come from each
feed, and the realtime service's flushes finish an utterance and start a new
stream. Speaker detection runs Sortformer over the same file and assigns its turns
to word times, or to timed segments when words are unavailable.

The transcribe.cpp catalog includes Whisper, Fun-ASR, Parakeet, Voxtral,
Nemotron Speech Streaming EN 0.6B, Qwen3-ASR 0.6B/1.7B and Cohere Transcribe
03-2026. Nemotron supports file and realtime transcription with file timestamps;
Qwen3-ASR and Cohere transcribe files without timestamps. Qwen3-ASR uses a
conservative 600-second chunk limit; Cohere uses its 400-second input limit.
Cohere requires an explicit supported language: Automatic and unsupported
languages fail with a prompt to choose a language before the engine is called.
Regional language tags use their primary subtag (for example, `en-GB` becomes `en`).

MOSS-Transcribe-Diarize is available in Q8_0 (987 MB) and Q5_K_M (700 MB),
with English/Chinese file transcription, automatic language selection, segment
timestamps and native speaker attribution; it does not support realtime.
The [transcribe.cpp 0.4.0 model documentation](https://github.com/handy-computer/transcribe.cpp/blob/v0.4.0/docs/models/moss-transcribe-diarize.md)
describes the supported checkpoint and its memory use. Kikoe conservatively splits
MOSS inputs at 600 seconds through the existing upload pipeline. Speaker labels are
local to each chunk; identities are not matched across chunks.

Catalog entries declare `native_diarization` when speaker attribution is built in.
These models offer speaker detection without Sortformer installed. The local client
passes the user's speaker switch through the worker to the engine's transcription
call, independently of timestamps, and skips the separate diarization pass. Other
timed models continue to use Sortformer when installed and requested.

`LocalModelManager` implements `storage.providers.ProviderSource`, so
`ProviderRepository.providers` includes a built-in “On This Device” provider
(`ApiKind.LOCAL`, ID `local`) while at least one model and its engine are
installed. `ProviderRepository.configured` excludes it, it is never written to
settings, and it needs no keyring entry. Clients are built through
`providers.factory.create_client(provider, key, ClientContext(local=…))`; the
application binds that context once and passes the factory to workflows.

## Realtime recording and recovery

`services.recording.RecordingSession` coordinates capture, local storage and
finalization. `services.realtime.RealtimeRecording` owns live connections;
`services.recording_workflow` reports connection failures while local capture
continues. Its finalizing callback runs after capture ends and before waiting for
live text, so the UI can show progress throughout that wait.

Native PCM is journaled before an independent sender reads it. A streaming ffmpeg
process resamples bounded buffers to the provider's mono little-endian int16
format. `core.transcription.RealtimeProfile` declares the sample rate, replay
checkpoint policy and connection lifetime, keeping the service provider-neutral.
OpenAI uses 24 kHz; the other live adapters use 16 kHz. Network delays cannot block
the capture writer, and retained audio stays at the device's native rate.

`core.transcription` models interim and final events. Adapters interpret wire
events; `LiveTranscript` replaces previews and repeated finals. A final clears
only its own utterance's preview, preserving a newer utterance still in progress.
OpenAI completions follow committed-item order. Deepgram retains every `is_final` chunk, even before
`speech_final`. Gemini restarts its finite receive iterator for each turn.
Metadata absent from an endpoint is never invented. OpenAI manual commits use PCM
energy, 500 ms silence and a 15-second speech limit.

Confirmed text is atomically synced to a versioned `<recording id>.transcript`
sidecar; previews are never persisted. Audio journals remain compatible with
version 1. Recovery restores confirmed text and audio as PENDING without contacting
a provider. Journals and sidecars are removed only after retained FLAC and the
database transaction are durable.

### Connection lifecycle

- Retryable failures reconnect after 1, 2, 4, 8 and then 15 seconds.
  Authentication and configuration failures stop retries.
- A local resampler failure stops live transcription as incomplete and reports
  the conversion error. Capture continues retaining audio for recovery or retry.
- Unconfirmed audio replays from the local journal at no more than 1.25 times
  realtime. Previously displayed final text stays visible until replay catches
  up and commits its replacement.
- OpenAI and Gemini flush boundaries establish replay checkpoints. Deepgram uses
  final-event audio ranges without adding display timestamps. Gemini rotates
  connections after nine minutes. Rotation cannot mark unsent audio complete,
  even after capture stops.
- Pause drains the resampler and finishes the connection. Resume starts at the
  confirmed native sample offset. Deepgram CloseStream waits for terminal
  Metadata, including for silent recordings. SDK closure has a one-second bound.
- Stop allows up to ten seconds for final text. Closing or quitting cancels
  network work immediately, then saves local audio. Incomplete sessions retain
  confirmed text and audio for explicit file-model retry. Successful live
  sessions do not upload the file again.

At startup, an alert offers Recover or Later for interrupted captures. Recovery
trims an incomplete final PCM sample, finalizes FLAC and appends to the original
note. If the note was deleted, it creates one from the journal title. Lookup by
recording ID makes recovery idempotent after a crash between database commit and
journal removal. Conversion or disk errors leave recovery files intact; damaged
or newer journals are reported without modification. Recovery never uploads audio
automatically.

## Speakers and playback

Recognition labels in `Segment.speaker_id` are scoped to a recording and a
recognition connection or file chunk. Independent labels never imply the same
person. `Recording.speaker_bindings` maps labels to note-wide `Speaker` objects
while retaining an independent `original_id`, allowing many labels to map to one
person and later be separated. Persistent, one-based `Speaker.ordinal` values
supply default names and four theme palette slots. `core.speakers` resolves
identities for display and export; legacy zero-ordinal speakers use first appearance
for numbering.

The first recording establishes people. Later independent labels require
explicit matching when a note already has speakers. Keeping a label as a new
speaker confirms that decision; cancelling writes nothing. Named identities
remain available after their original transcript is replaced.

Re-transcription atomically replaces text, model metadata and bindings after all
chunks succeed. It retains the previous state on failure or cancellation. Identity
preservation requires valid timed speech: at least 80% of a new label's speech
must overlap an old person, with a strictly better score at least twice the
runner-up. Missing or invalid times require manual matching. Preserving an
unconfirmed identity does not confirm it.

`media.player` uses sounddevice `RawOutputStream` at the selected device's native
rate. `media.ffmpeg.decode_audio` supplies bounded mono int16 blocks and drains
decoder diagnostics concurrently. Device calls run in worker threads; cancellation
waits for an in-flight call before closing the stream. The window permits one
playback task and stops it on note changes, dialog or window closure, and before
capture or transcription. No external player or additional playback library is
required.

Input and output preferences store device names, with `None` meaning System
Default. `media.devices` resolves names to PortAudio indices. On Linux, desktop
aliases such as `pipewire`, `pulse` and `default` take priority over PortAudio's
first hardware device. A missing saved device falls back to the system default.

## Interface coordination

The main window uses `Adw.NavigationSplitView` and collapses below 640 sp. Its
content stack holds welcome, empty and note views, with the bottom bar below.
The window constructs views, connects controllers and registers `win.*` actions;
controllers run workflows through a narrow `ui.window_host` protocol. The sidebar
uses a smaller set of callbacks.

| Component | Responsibility |
| --- | --- |
| `ui.note_sidebar` | Notes list, search, context menu and selection |
| `ui.note_actions` | Create, rename and delete notes and recordings |
| `ui.segment_editor` | In-place edits and note-wide Undo/Redo through `services.editing.EditingSession` |
| `ui.export_controller`, `ui.file_dialogs` | Copy, export and save dialogs |
| `ui.speaker_controller` | Speaker naming, matching and samples through `services.playback.Playback` |
| `ui.transcription_controller` | Import, retry and re-transcription through the file workflow |
| `ui.recording_controller` | Capture controls and startup recovery |
| `ui.window_chrome` | Header layout and debounced geometry saving |

`core.activity.Stage` is the one foreground step (idle, recording, stopping,
finalizing, saving audio, converting, transcribing, saving, cancelling,
recovering). Controllers call `WindowHost.set_stage`; the window stores the stage
in the tracker, refreshes actions and tells the bottom bar, which only renders it.
`core.activity.Activity` describes current work including the stage;
`core.activity.action_availability` derives enabled actions, so a stage such as
stopping or cancelling keeps actions disabled during cleanup. Only one foreground
task runs at a time, so re-transcription also waits for running work.
`services.activity.ActivityTracker` coordinates shutdown: save edits, stop
playback, stop capture, cancel imports, and let recovery already writing audio
finish. An import already cancelling is awaited without cancelling it again, so
its asynchronous cleanup can finish. Saved-note callbacks
carry note identity so an old write cannot replace a newly selected note. Export and shutdown wait for pending edits.

`core.open_requests` tags note reads with the current selection generation.
Failed or cancelled reads release their own pending selection so automatic
reloads can continue; older reads cannot release or replace a newer selection.

`ui.note_view` renders a flat `Gtk.ListView` of recording headers and segments.
Only visible rows need widgets. Reusable items, hidden spare rows and a bounded
widget cache avoid rebuilding the list on note changes or sidebar folding. Visible
rows bind immediately; remaining rows update over later frames.
`ui.segment_text` measures text height for the available width without per-row
frame callbacks. Text saves after a pause, focus loss or recycling; text-only
updates preserve the caret. `services.editing.EditingSession` applies an edit to
its copy of the note at once (`core.edit_history.apply_segment_fields`), saves it
in the background one write at a time, and lays values not saved yet over a note
read again meanwhile. The window then updates only the edited row:
`ui.transcript_rows.SegmentIndex` finds it from its recording header's position,
so an edit's cost does not grow with the transcript. `core.edit_history` owns
history, `core.transcript_rows` decides row changes, `ui.transcript_rows` builds
rows and overlays, `ui.stale_rows` binds off-screen rows over later frames, and
`ui.row_focus` moves the caret.

`ui.file_transcription.FileTranscriptionDisplay` owns preview snapshots and refresh
scheduling, restoring the latest preview when the target note is revisited.
`ui.recording_view` provides the batch placeholder; live transcription shares the
note list. The bottom bar owns model selection and receives changes from
`ui.transcription_dialog`, which pushes `ui.model_chooser_page` to list and search
models with `core.choice.model_matches`; `ui.retranscribe_dialog` pushes the same
page with file models only. `ui.level_meter` draws 28 theme-colored bars from PCM
RMS measurements in `core.recording`.

Widgets pair Python behavior with Blueprint layout. Custom children are inserted
into `Adw.Bin` slots when Blueprint cannot inspect their types. Compiled templates
load from disk through `ui.templates`; packaging needs no GResource build.
Bundled icons in `ui/icons/hicolor` join the theme search path at startup.

## Search, localization and diagnostics

Note search uses parameterized SQLite `instr` expressions and a Python `casefold`
function over titles and transcript text. Wildcards are literal. Searches scan
text without an FTS index and return only matching summaries in newest-first
order. `NoteRepository.search` runs off the GTK thread. `ui.search.NoteSearch`
debounces input by 200 ms, cancels old tasks and rejects stale results.

Model selection keeps the current choice when valid, then tries recent,
first configured model, and finally none (`core.choice.resolve_choice`). Explicit
selection is persisted. Each workflow captures language and options at request
start; preference changes apply when controls return idle.

Python gettext translates Python strings and marked GtkBuilder properties,
including contexts. `ui_file` creates a content-addressed translated template in
`paths.cache_dir()/ui`, removing translation markers to prevent double
translation. Original templates stay untouched. This works without native
`locale.bindtextdomain` on macOS and Windows. Cache failures log a diagnostic and
fall back to original templates and native GTK translation.

Language loading preserves gettext environment precedence and ordered fallbacks,
normalizes BCP 47 tags, and maps Chinese regional locales to `zh_Hans` or `zh_Hant`.
Explicit scripts take priority; bare `zh` uses Simplified Chinese. Startup sets
GTK text direction from the loaded catalog, including Arabic fallback selections.
`po/LINGUAS` lists supported catalogs. Interface language follows the system;
there is no application override. Build catalogs and templates before packaging.

`diagnostics.configure_logging` writes terminal output and
`paths.log_dir()/kikoe.log`, rotating at 2 MiB with three backups. It retains numeric
arguments and exception types, redacts other dynamic values, and omits third-party
message bodies. Tracebacks retain filenames, line numbers and function names,
excluding source text, exception messages and locals. Unexpected errors are
recorded locally. Reports are never uploaded automatically; users choose which
files to attach.

## Dependencies and provider extension

Runtime dependencies include `httpx` for SDK transports, official provider SDKs,
`keyring` for credentials, and `sounddevice` with PortAudio for audio. The exact
versions and extras are defined in [pyproject.toml](../pyproject.toml) and
[uv.lock](../uv.lock). Add a dependency only with the feature that requires it,
following [AGENTS.md](../AGENTS.md).

A new local engine needs a package in `providers/local/engines/` with a catalog and an
adapter, and one registry line; no other module changes. A new cloud API consists of
a registry descriptor, client and model presets. Callers
must not branch on `ApiKind`; architecture tests enforce this outside the registry.

1. Add a member to `core.models.ApiKind`. Its value is persisted; never rename it.
2. Add a `ProviderDescriptor` to `core.provider_registry`, including name, base
   URL, upload policy, language restrictions, hints and SDK logger names.
3. Implement `TranscriptionProvider` in `providers/<name>.py`, using
   `providers.base.HttpClient` or `FileOnlyClient` as appropriate. Register it in
   `providers.factory.CLIENTS` and declare the realtime profile when needed.
4. Add the API fallback and model rules to `storage/model_presets.json`.
5. Test the client with recorded or fake responses. Registry, factory and profile
   completeness tests cover integration. Update translation catalogs with
   `uv run scripts/i18n.py update` only when introducing UI strings.
