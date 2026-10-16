# UI design specification

Kikoe presents audio and transcripts as editable notes. The interface keeps
recording, transcription and review in one window, with provider configuration
in Preferences. This document defines the interface behavior and layout; the
[architecture reference](architecture.md) describes its implementation.

Use native GTK 4 and libadwaita controls, system fonts and theme colors. Layouts
must remain usable at 360 px wide, with keyboard navigation, translated labels
and right-to-left text. Dimensions below describe the normal desktop layout;
smaller windows adapt rather than removing essential actions.

## Terms

| Term | Meaning |
| --- | --- |
| Note | A sidebar item containing one or more recordings in order |
| Recording | Recorded or imported audio, its retained original and its transcript |
| Segment | A transcript passage with optional timestamps and a speaker |
| Provider | A configured cloud service with a name, API type, URL, key and models, or the built-in On This Device provider of installed local models |
| Model | A provider's transcription model, with separate file and realtime capabilities |

File capabilities include timestamps, word timestamps, speaker detection and
streaming previews. Realtime capabilities describe text appearing during capture,
with optional timestamps and speakers. Bundled presets supply defaults for known
models and disable options an endpoint rejects. Other options remain editable.

Appending audio adds a recording to a note. Speaker labels from independent
recordings or recognition connections require explicit matching; identical
provider numbers do not establish identity.

## Main window

The default window is 1180×780, with an approximately 260 px sidebar and a content
pane. The sidebar uses the theme's sidebar background; the content uses its view
background. Transcript width is limited to 760 px for comfortable reading.

### Sidebar and search

The sidebar header contains Kikoe, New Note, Search Notes and Main Menu. When
system window controls occupy the start of the header, New Note moves alongside
the other application controls at the end, and the title hides to avoid crowding.
New Note is unavailable until a provider exists.

Rows show the note title and a concise subtitle with date, duration or recording
count. New recordings appear at the top with a Recording status. Secondary click
or long press offers Rename… and Delete Note without opening the note. The target
row stays highlighted while its menu is open; closing the menu preserves the
selected note.

Search Notes toggles a search bar; typing in the list also starts search when no
capture or import is running. Search matches literal, case-insensitive substrings
in titles and transcripts. An empty query restores all notes. Filtering preserves
the open note, and Escape closes search. No matches show No Matching Notes with
`edit-find-symbolic`. Search is disabled during capture and transcription so
narrow layouts retain access to Stop.

### Content header and menus

The content header shows the note title and a subtitle such as
“2 recordings · 1:39”. Undo and Redo appear at the start; Display Options and
Note Menu appear at the end.

Display Options contains Show Timestamps and Show Speakers. These affect display
and export, without altering stored transcript data. Note Menu contains Rename…,
Copy All Text, Export… and destructive Delete Note. Keeping Export in the menu
leaves room for editing controls at narrow widths.

Export opens a dialog with four formats and options for timestamps and speaker
labels, then a save dialog. Subtitle formats are unavailable when the note has
no timestamps. Export waits for pending edits.

### Recordings and transcript rows

Each recording has a heading, Play This Recording control and Recording Menu,
with a dimmed metadata line beneath. Metadata includes date, duration, provider,
model and transcription options; appended recordings indicate that they were
appended. Spacing separates sections without an extra separator.

Recording Menu offers Re-transcribe…, Export Original Audio…, Match Speakers…
and Delete Recording. Playback controls switch between play and stop. Only one
recording or sample plays at a time.

Each segment places a bold, colored speaker name above a small, dimmed timestamp
in a metadata column, with transcript text on the right. Speaker names open
Rename Speaker. Hiding both speakers and timestamps removes the metadata column.
Below 520 sp, metadata moves above the text on one line. Timestamps use tabular
digits through the `numeric` style class.

Speaker colors use four GNOME palette slots, assigned by persistent person
number. Renaming or confidently preserving an identity retains its color.
Unnamed people appear as Speaker N. Color supplements the name rather than
being the only way to distinguish a person.

### Editing text and timestamps

Segment text is a borderless text view with a subtle hover tint and an accent
tint while it holds the caret. Editing happens in place. Text saves after a short
typing pause and when focus leaves the segment. Empty text is rejected: the saved
text returns and a toast explains that segment text cannot be empty.

| Key | Behavior while editing |
| --- | --- |
| Enter | Save and move to the next segment |
| Shift+Enter | Insert a line break |
| Escape | Revert typing that has not been saved |
| Up/Down at the first/last line | Move between segments |

An active input-method composition owns these keys until composition ends.

The timestamp is a flat button, labeled Add Time when absent. It opens a compact
popover with Start and End entries, mnemonic labels and a format hint. Accepted
forms include MM:SS, H:MM:SS and decimal seconds such as `00:26.860`; an empty
entry clears that time. Tab moves between fields.

Valid times apply on Enter or when the popover closes, including Escape or an
outside click. Invalid entries show an error style and a specific explanation.
Enter keeps the popover open and focuses the first invalid field. Closing with
invalid input restores saved times and reports that the changes were not saved.

Undo and Redo cover text and time edits across the note. Quick successive saves
of one segment form one step. History clears on a note change or when recordings
are added, removed or re-transcribed. Undo and Redo scroll to the affected segment
and place the caret there. Recording, import and recovery make transcript text
read-only and disable history actions. Live preview rows are never editable.

### Bottom bar

The bottom bar belongs to the content pane and appears only when a note is open.
It uses the libadwaita `toolbar` style, remains one line at a stable height and
shows exactly one of three layouts, chosen by the window's current stage
(`core.activity.Stage`). Every layout reads the same way: what is happening at
the start, a flexible gap, then actions at the end. Secondary actions are flat;
the one primary action uses `suggested-action`. There are no custom chips.

| Stage | Layout |
| --- | --- |
| Idle | Transcription Model (service icon, bold model name, dimmed provider name); flat Import Audio…; primary Record for an empty note, Append Recording otherwise |
| Recording | Pulsing record dot, elapsed time, level meter, dimmed model and options summary; flat Pause or Resume; primary Stop |
| Stopping, finishing transcription, saving the recording, converting, transcribing, saving, cancelling, recovering | Spinner and dimmed progress text; flat Cancel where cancelling makes sense |

Cancel appears for converting, transcribing, saving and cancelling. It is
enabled only while converting or transcribing: Saving is atomic and Cancelling…
disables repeated requests. Stopping, saving a recording and recovering show
progress only, because they must finish to keep the audio.

Below 600 sp, the provider name, the Import Audio label and the recording
summary hide; icon-only buttons keep their tooltip and accessibility name. Below
480 sp the level meter hides too. The primary button keeps its label at 360 px.
Import Audio is also available through the main menu when no note is selected;
invoking it opens the content pane to show progress and Cancel.

## Empty states

| State | Content and next action |
| --- | --- |
| No notes | Sidebar status No Notes Yet with `document-edit-symbolic` |
| No providers and no notes | Welcome to Kikoe, app icon, explanation of cloud and local transcription, primary Add Provider and secondary Use a Local Model… (opens Preferences → Local Models) |
| Providers exist, no note selected | No Note Selected, `document-edit-symbolic`, instruction to create a note and primary New Note |
| Search finds no notes | No Matching Notes with `edit-find-symbolic` |

The welcome message explains that a provider or local model is needed before recording
or importing. The app icon is reserved for the welcome page. Empty-state primary
actions may use the standard suggested pill style. The bottom bar stays hidden
until a note opens.

## Model selection and transcription options

Transcription Model opens an approximately 440 px dialog. Changes apply
immediately. Each provider has a group of model radio rows, showing a model name
and capability summary such as “Realtime · Timestamps · Speakers” or “Text only”.
Initial focus is on the current model.

The Options group explains whether transcription happens during or after
recording. Timestamps and Speakers switches request those features for the next
session. A model without a capability disables its switch and explains “Not
supported by this model”; the earlier choice returns with a supporting model.
When word timestamps are available and timestamps are enabled, Timestamp Detail
offers Segments, Words, and Segments and Words.

If an upload must omit a configured language because Mistral cannot combine it
with timestamps or speakers, a visible caption explains automatic language
detection. It disappears when both options are off and does not apply to live
recording. Manage Providers and Models… closes the picker and opens Preferences.
The model button's tooltip includes the model, provider and capability restrictions.

Most recently selected model takes priority on restart, followed by the default
model and then the first configured model. Missing choices fall back in that
order. Changing the default selects it when idle. Running tasks retain the model,
language and options captured at their start.

## Importing and file transcription

Import Audio… appends to the open note or creates a note named after the file.
Progress and Cancel appear in the bottom bar; failures that need explanation use
a dialog. Clicking Cancel immediately shows Cancelling… and disables repeated
cancellation until resource cleanup finishes.

Models with Streaming Preview show temporary text during imports, stopped
recording uploads, retries and re-transcription. Completed segments use normal
text; unfinished text is dimmed with an accent-colored blinking caret. Scrolling
follows new text only when the reader is already at the bottom.

An import into an existing note appends a temporary recording section. Without
an open note, it shows a temporary page named after the file. The sidebar gains
no note until import succeeds. Retry and re-transcription show new text in the
original recording's position, preserving neighboring recordings.

Preview rows cannot be edited, played or used for speaker matching. Failure or
cancellation removes them and restores existing text; success displays the saved
result. Cancel is disabled during the final atomic save. Models without file
streaming continue to use ordinary file requests.

## Recording

Starting capture creates or appends a recording. The header and sidebar show
Recording, and the section metadata identifies the model, options and current
connection state.

Realtime models show segments while capturing. The last, unfinished line is
dimmed with a blinking caret. Existing and live recordings share the transcript
list, preserving the reader's scroll position. Metadata indicates connecting,
reconnecting or catching up. The recording bar also shows a spinner while
transcription prepares (including local model loading), reconnects or catches up;
capture controls remain available. At narrow widths the spinner remains visible
when the summary is hidden.

Stop immediately replaces the capture controls with a spinner and Stopping
recording…, followed by Finishing transcription… while realtime text drains and
Saving recording… during persistence. These stages offer no Cancel button.
Displayed live text stays visible until the saved recording replaces it.

File-only models show a centered microphone icon in an 88 px circle, a Recording
heading, and an explanation that Kikoe will upload and transcribe after Stop.
Existing recordings remain above this section. Native live endpoints for OpenAI,
Deepgram, Gemini and Mistral use the realtime view when the selected model supports
it.

### Capture controls

The recording bar replaces idle controls with, in order, a recording indicator,
elapsed time, 28 live level bars, model/options summary, icon-only Pause Recording,
and a labeled Stop button. The 10 px recording indicator pulses when animations
are enabled. Level bars are 3 px wide and up to 28 px high, driven by actual audio.
Stop uses `suggested-action` because it saves the recording.

Pause freezes the timer, clears the meter, dims the indicator and changes status
to Paused. Its button becomes Resume Recording. Resume continues the same local
recording, excluding paused time from both duration and audio.

The bar stays one line. Below 600 sp the summary hides; below 480 sp the meter
also hides, leaving timer, pause/resume and labeled Stop usable at 360 px. During
capture below 480 sp, the content header retains only the system-side Close
control. Theme colors remain authoritative.

During capture, saving and transcription, conflicting actions are unavailable:
new capture, import, note changes, deletion of the target note and export. Display
Options remains available. Session model and options cannot change mid-capture.

### Stop, interruption and recovery

Stop retains original FLAC. File models then upload it automatically; live models
wait up to ten seconds for final text and save without a second upload. Incomplete
live sessions retain audio and confirmed text, show Transcription incomplete, and
offer Retry Transcription.

Retry uses the current file model and options, replacing the same recording only
on success. Failure or cancellation preserves existing text. Live-only models
disable import and retry and explain that a file model is required; Kikoe never
substitutes one automatically.

Pause drains accepted audio and closes the live connection. Resume starts a new
connection from the confirmed audio position. Its independent speaker labels do
not automatically match earlier ones.

Closing or quitting cancels network work and saves local audio. On startup,
Recover or Later is offered for interrupted captures. Recovery restores audio and
confirmed text to the note without uploading; transcription requires explicit
retry. Failed conversion leaves recovery files available for another attempt.

## Speaker matching and re-transcription

An unmatched-speaker banner identifies the first appended recording needing a
decision and offers Match Speakers…. Confirming that recording advances the banner
to the next one.

### Match Speakers

The approximately 520×520 dialog has Cancel, Match Speakers and suggested Apply
in its header. Its introduction explains that independent recordings number
speakers separately and asks the user to listen and choose identities.

Each row contains a sample playback button, colored speaker name and dimmed
quotation, then a selection menu with check marks. Existing Speakers lists
note-wide candidates, including named identities retained after re-transcription.
Keep as New Speaker preserves an independent identity. New labels initially show
New Speaker; Apply confirms all choices, including keeping them separate. Cancel
writes nothing. Multiple labels may map to one person and can be separated later.

Samples use the first nonempty turn with reliable timing, last at most ten seconds,
and stop at the next timestamp or recording end when needed. Without reliable
times, the quote remains available and matching works, but playback is disabled.
Playing a sample stops previous playback; activating it again stops it. Closing
the dialog stops audio. Content scrolls at narrow widths.

### Re-transcribe

The approximately 480×520 dialog has Cancel, Re-transcribe and suggested Transcribe
in its header. It identifies the recording and duration, explains use of retained
original audio, and warns that replacement includes manual edits. Other recordings
remain intact, and speaker names are preserved where possible.

A Model row opens the picker. Timestamps, Timestamp Detail and Speakers use the
selected model's file capabilities. The picker starts with the bottom bar's model
only if it supports files; live-only selections require an explicit file model.
Initial option switches reflect the current transcript.

Only complete success replaces text, model information and speaker bindings.
Failure or cancellation preserves the old recording, identities, audio and status.
Progress uses the bottom bar and conflicting edit, import and capture actions are
disabled. Cancel is disabled during the final save; closing waits for that save.

Names are preserved only with strong timing evidence: 80% speech overlap and a
score at least twice the runner-up. Provider numbers are not identity evidence.
Uncertain or previously unconfirmed matches still require a decision. Warnings
use theme colors, and dialog content scrolls at 360 px.

## Preferences

Preferences uses an approximately 640×600 `Adw.PreferencesDialog` with General,
Providers and Local Models pages and standard grouped rows.

### General

| Group | Controls and behavior |
| --- | --- |
| Transcription | Default Model and Transcription Language; empty language means automatic detection; visible examples include `zh`, `en` and `ja`; invalid codes are not saved |
| Audio | Microphone and Audio Output; System Default is first and initially selected |
| Storage | Data Folder shows and opens the current location; relocating the library is unavailable |
| Troubleshooting | Log Folder opens local diagnostics and explains that users choose what to attach to reports |

Device choices are saved by name. An unplugged selection remains listed as
“<name> (Unavailable)” while the system default is used. A running recording keeps
its starting device.

Interface language follows the system, independently of the transcription
language. English and 14 translations are included: Simplified and Traditional
Chinese, Spanish, French, German, Japanese, Korean, Brazilian Portuguese, Russian,
Italian, Arabic, Hindi, Indonesian and Turkish. Regional Chinese locales map to
the appropriate script; Arabic uses right-to-left layout.

### Providers and connection details

The Providers group explains cloud transcription and that each provider can offer
several models. Add Provider is a standard header button with a plus icon. Provider
rows show an initial avatar, name, API type and model count, and navigate to detail.

Provider detail is a scrollable navigation subpage titled with the provider name,
normally about 640×780. Its groups are:

- **Connection:** Name, API Type, Base URL and a masked API Key with Show Key.
  API types include OpenAI, OpenAI-compatible, OpenRouter, Deepgram, Gemini AI
  Studio, MiMo, Groq and Mistral. API type determines request format; Base URL
  identifies the endpoint.
- **File Transcription:** Parallel Requests, Chunk Length (Minutes), Chunk Size
  (MB), Audio Format and Reset Transcription Defaults. Defaults and maximums
  depend on the API; Gemini applies its shorter metadata duration limit at
  request time. See [Provider upload settings](upload-settings.md).
- **Models:** Model names and capability summaries, an Add Model header button
  and Edit Model controls.

Remove Provider is a full-width destructive row at the end. Existing connection
fields apply on confirmation, such as Enter or the row's apply button; changing
API type also applies immediately. Upload-setting rows apply immediately. New
providers use Add in the header to commit their configuration.

Use stock `Adw.NavigationPage`, `Adw.EntryRow`, `Adw.PasswordEntryRow`,
`Adw.ComboRow`, `Adw.SpinRow` and `Adw.ButtonRow` controls.

### Local Models

Local models transcribe on this device; nothing is uploaded. An introduction says
so and that an engine plus at least one model are needed. The page lists:

- **One group per engine** (currently transcribe.cpp), titled with its name. The
  description shows “Not downloaded · <size>”, download progress, or “Version
  <version> · Ready”/“Starting…”/“Could not start”. The header holds Download,
  Cancel, or a Remove Engine… icon button. **Acceleration** chooses the build:
  Vulkan (Most GPUs), CUDA (NVIDIA), Metal (Apple GPU) or CPU Only, as available
  for the platform. Vulkan, or Metal on Apple Silicon, is preselected; CUDA never
  is, because it is about ten times larger. Choosing another build after install
  turns Download into Switch. **Device** appears once the engine has started and
  lists Automatic plus the devices it reported. A dimmed note explains CUDA's
  driver requirement and, in Flatpak, the `flatpak override --user --device=all`
  command, because the sandbox hides NVIDIA devices by default.
- **Models**, one row per model with size, languages and Timestamps/Realtime
  tags, and Download, Cancel or Remove buttons. A downloaded model whose engine
  is missing says “Needs <engine>”. The speaker detection model is listed here
  too and adds Speakers to models with timestamps.

Removing an engine asks whether to keep its models; keeping them is the default
destructive choice, and a second response removes the models as well, with their
total size. A removed model needs no confirmation beyond its own dialog. On
macOS the engine is included with Kikoe: it has no Remove button and its
acceleration cannot change.

Installed models appear in model pickers as “<model> · On This Device”. The
Providers page does not list this built-in provider and it needs no API key.

### Add or edit a model

The approximately 480×600 Add Model dialog has Cancel and suggested Add, Model ID
and Display Name entries, and a What This Model Supports group. File transcription
and transcription while recording use expander rows with mode switches.

File options are Timestamps, Word Timestamps, Speaker Detection and Streaming
Preview. Realtime options are Timestamps and Speaker Detection. Explanations
clarify that file transcription includes imports and audio uploaded after Stop,
whereas realtime shows text during capture.

Typing a model ID applies bundled presets until the user changes an option.
Editing an existing model retains saved choices. Unsupported options are disabled
with an explanation. When a model lacks one mode, that mode is disabled and the
other remains on without a switch. Both modes off cannot be saved. Provider
summaries and model-picker hints use the same capabilities.

Rename Speaker and Add/Edit Model ask Discard Changes? when Escape or an outside
click would close a dialog with unsaved input. Cancel discards immediately.

## Keyboard, accessibility and feedback

Shortcuts use the same actions and availability rules as buttons. Command replaces
Ctrl on macOS.

| Action | Shortcut |
| --- | --- |
| New Note | Ctrl+N |
| Search Notes | Ctrl+F |
| Import Audio | Ctrl+O |
| Rename Note | F2 |
| Export Note | Ctrl+Shift+E |
| Undo | Ctrl+Z |
| Redo | Ctrl+Shift+Z or Ctrl+Y |
| Preferences | Ctrl+comma |
| Keyboard Shortcuts | Ctrl+? |
| Close Window | Ctrl+W |
| Quit | Ctrl+Q |

Keyboard Shortcuts opens `Adw.ShortcutsDialog`. Close and Quit use the same local
save path. Escape closes search and standard dialogs; it never stops recording.
Timestamp popovers apply the validation behavior described above, and unsaved
form dialogs require a discard decision.

Icon-only controls have translated accessibility names and tooltips. Play/Stop
and Pause/Resume names track their current actions. Dialogs are keyboard navigable
and scroll at narrow widths. Visible errors include explanatory text alongside
color.

Use dialogs for capture and transcription failures requiring explanation, and
short toasts for playback problems or missing files and folders. Dialogs carry
longer details. The window remembers size and maximized state. Use system fonts,
semantic style classes and Adwaita accent/destructive colors rather than fixed
fonts or color values.
