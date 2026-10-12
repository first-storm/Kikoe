# UI design specification

This is the reference for Kikoe's user interface. It was transcribed from the HTML
design draft `Kikoe.html` (Chinese UI; available locally, not committed, as
the visual reference for layout and spacing); strings here are the English source
strings. Follow the GNOME HIG and use stock libadwaita widgets wherever possible. The
"Widgets" lines are suggestions, not requirements.

Layout details from the draft that the implementation follows:
- Content pane on the view background (white), the sidebar on the sidebar background.
- Transcript column at most 760 px wide. Recording header on one line: bold
  "Recording N", then the dimmed meta line, then (later) the recording buttons, with a
  separator below. Each segment has a left column with the speaker name (bold, colored)
  above the timestamp (small monospace, dimmed), and the text on the right in slightly
  larger type. The left column disappears when both timestamps and speakers are hidden.
- Bottom bar (below the content pane only): model button with a cloud icon, the model
  name in bold and the provider dimmed; chips; hint; then right-aligned "Import Audio…"
  (flat, folder icon) and the primary "Append Recording" pill (mic icon).
- "Add Provider"/"Add Model" header buttons are ordinary buttons with a + icon; removal
  is a full-width destructive row at the end of the page.

Sample data used throughout: speakers "Kobayashi" (blue `#1c71d8`), "Joe" (orange
`#b34700`); unmatched speakers get "Speaker 3" (green `#2b7a4b`), "Speaker 4"
(purple `#813d9c`). Speaker colors come from the GNOME palette. Persistent person numbers determine colors;
renaming and confidently preserving an identity keep its color.

## Concepts

- **Note**: the unit in the sidebar. Holds one or more **recordings**, shown in order.
  Recording more into an existing note *appends* a recording.
- **Recording**: one piece of recorded or imported audio, plus its transcript. The
  original audio is kept so it can be re-transcribed or exported.
- **Segment**: a line of transcript, optionally with a timestamp and a speaker.
- **Provider**: a configured cloud service (name, API type, base URL, API key) holding
  one or more **models**.
- **Model capabilities**: *realtime* (text appears while recording), *timestamps*,
  *speaker diarization*. The user ticks these when adding a model; the UI disables
  options a model cannot do.

## 1. Main window — note with appended recording

Default size 1180×780. Two panes.

**Sidebar** (≈260 px, "Notes"):
- Header: app title "Kikoe"; buttons *New Note*, *Search Notes*, *Main Menu*.
- List rows: note title + subtitle with date and length, e.g.
  - "Podcast Topics" — "Today 15:12 · 2 recordings"
  - "Group Meeting" — "Yesterday · 42:10"
  - "Interview Material" — "Oct 9 · Imported audio · 26:31"
  - "Voice Memo" — "Oct 8 · 0:47"

**Content header**: note title ("Podcast Topics") with subtitle "2 recordings · 1:39".
Header buttons:
- *Display Options* (popover):
  - Switch "Show Timestamps", switch "Show Speakers".
  - Footnote: "Only changes how this note is shown and exported. The transcript is not modified."
- *Export* (menu): "Plain Text (.txt)", "Markdown (.md)", "SRT Subtitles (.srt)",
  "WebVTT Subtitles (.vtt)". Footnote: "Speaker labels follow the current display
  settings. Subtitle formats always include timestamps."
- *Note Menu*: "Rename…", "Copy All Text", "Delete Note" (destructive).

**Banner** (when an appended recording has unmatched speakers):
"Recording 2 was appended and its speakers are not yet matched with Recording 1" with
button "Match Speakers…" → opens screen 4. Widgets: `Adw.Banner`.

**Recordings**, each as a section:
- Header: "Recording 1" + meta line "Today 14:30 · 0:58 · Deepgram nova-3 ·
  Timestamps, Speakers"; appended ones say "Today 15:12 appended · 0:41 · …".
- Header buttons: *Play This Recording*, *Recording Menu*: "Re-transcribe…",
  "Export Original Audio…", "Delete This Recording" (destructive).
- Segments: speaker name (colored, clickable → "Rename Speaker"), timestamp
  (`00:09`, monospace), text. Speaker/timestamp columns hide per display options.

**Bottom bar** (recording controls):
- *Transcription Model* picker button showing "nova-3 · Deepgram". Popover lists models
  grouped by provider; each row shows name + capability summary ("Realtime ·
  Timestamps · Speakers", or "Text only"), check mark on the current one. Footer item
  "Manage Providers and Models…" → Preferences.
- Toggle chips "Timestamps" and "Speakers": whether the next recording requests them.
  If the model lacks the capability, the chip is disabled and struck through, tooltip
  "<model> does not support <capability>". Enabled tooltips: "Generate <x> while
  transcribing (click to turn off)".
- Hint text: "Transcribes while recording" or "Transcribes after recording", plus
  "· This model does not support <list>" when relevant.
- Buttons: "Import Audio…" and the primary "Append Recording" (on an empty note,
  "Record").

**Implementation status**:
- Implemented: sidebar list with *New Note* (disabled until a provider exists);
  content header with title and subtitle; *Display Options*; *Note Menu* with "Rename…",
  "Copy All Text" and "Delete Note"; *Export* with all four formats, a save dialog, and
  subtitle buttons disabled when the note has no timestamps; recordings and segments
  with speaker colors (unnamed speakers show as "Speaker N"); recording header menu
  with "Export Original Audio…"; the bottom bar's model picker, Timestamps/Speakers chips
  (they set what the next transcription requests), hint text, and "Import Audio…", which
  adds to the open note or creates one named after the file, with progress, Cancel and a
  failure toast; Record/Append Recording, pause/resume, Stop, live level meter, batch
  transcription after stopping, retained-audio retry, and startup recovery.
- M6 implemented: unmatched-speaker banner; recording playback/stop; clickable colored
  speaker names with Rename Speaker and Match Speakers; reversible many-to-one matching
  with quoted samples and playback; Re-transcribe with an independent model/options picker.
- M7 implemented: Search Notes opens a sidebar search bar; title and transcript matches
  are case-insensitive literal substrings. Empty queries restore all notes; no matches
  show "No Matching Notes". Filtering preserves the open note. Escape closes search.
  The sidebar is approximately 260 px at desktop sizes; search precedes the main menu.
- At 360 px, idle controls wrap at their natural widths so action labels remain legible.
  The provider suffix and mode hint hide below 480 sp; model and feature tooltips retain
  their explanations. Import Audio is also available in the main menu without a selected note.
- Not yet: "Delete This Recording" (outside M7).

## 2. Recording in progress

- New note appears at top of the sidebar as "Oct 12 15:40 Recording" with subtitle
  "Recording" (red dot).
- Content header shows the note title and "Recording".
- Section "Recording 1", meta "In progress · Deepgram nova-3 · Timestamps, Speakers".
- **Realtime model**: segments stream in ("Speaker 1 00:02 …"); the last line is
  partial (shown dimmed with an inline accent-colored blinking caret until finalized).
  Existing recordings and live rows share one virtualized list. Updates follow the
  bottom only while the user is already there; reading older text preserves scrolling.
  The metadata line shows connecting, reconnecting and catch-up states.
- **M4 batch mode**: centered 88 px circle containing a microphone icon, "Recording"
  heading, and "Kikoe will upload the audio to {model} and transcribe it automatically
  after you stop. The text will appear here." Existing recordings remain above the
  appended recording section. Models without effective realtime support use this branch.
  Deepgram, OpenAI and Gemini native live endpoints use the realtime branch.
- Bottom bar follows the HTML's order: 10 px red recording indicator, elapsed time
  `00:31` (monospace/tabular digits), 28 live level bars (3 px wide, up to 28 px high),
  model/options summary, round icon-only "Pause Recording" button, and a destructive
  "Stop" pill with stop icon. The normal model/import/record controls are replaced.
- Pause changes the central heading and header/sidebar subtitle to "Paused", freezes
  the timer, clears the meter, and switches the pause control to "Resume Recording".
  The red indicator dims while paused. Resume appends audio to the same recording.
- At 360 px, the bottom controls wrap; pause/resume and Stop stay together and visible.
  At 1180×780, the recording controls stay on one line. Below 480 sp, the content
  header keeps only the close window decoration so all app controls still fit. Colors
  follow the Adwaita theme. The red recording dot pulses while animations are enabled.
- During recording/saving/transcription, starting another capture, importing, changing
  notes and deleting the target note are disabled; exporting is disabled. Display
  options remain available. The selected model/options are fixed for the session.
- Stop saves original FLAC. Batch models then upload it automatically. Live models
  drain their final response for at most ten seconds and save the streamed transcript
  without another upload. Partial live text and audio are retained on failure, with a
  "Transcription incomplete" message and the existing "Retry Transcription" button.
- File-only retry uses the current model/options and replaces the same recording after
  success; cancellation or failure preserves its existing text. Microphone-only models
  disable Import Audio and Retry Transcription; their import tooltip explains that a
  file model is required. No model is substituted automatically.
- Pause drains the accepted audio and closes the live connection. Resume opens another
  connection and sends only the unsent local samples. The microphone timer excludes
  the pause. A new recognition connection does not assume its speaker IDs match the old
  one. Closing or quitting cancels network work before finalizing local audio.
- Closing or quitting stops and saves audio; pending transcription can be retried later.
  Startup offers "Recover" or "Later" for interrupted captures. Recovery only restores
  audio and the note; uploading requires an explicit retry. Failed conversion leaves
  files intact so startup recovery can be attempted again.

## 3. First run (no providers, no notes)

- Sidebar empty state: "No Notes Yet".
- Content: status page with app icon, title "Welcome to Kikoe", description "Kikoe
  turns speech into text using cloud services. Add a provider and a model first, then
  record or import audio.", suggested pill button "Add Provider".
- **Implemented** (`ui/window.blp`).

## 4. Dialog: Match Speakers (≈520×520)

- Header: "Cancel" · title "Match Speakers" · suggested "Apply".
- Intro: "Recording 2 was appended later and its speakers were numbered again, so they
  may not match the people in Recording 1. Listen to the samples and choose who they are."
- One row per new speaker: colored name ("Speaker 3"), a quoted sample sentence, a
  *Play sample* button ("Play Speaker 3's sample"), and a dropdown "Who is Speaker 3?"
  with options: section "People in Recording 1" → each existing speaker (colored);
  then "Keep as New Speaker".
- Footnote: "Two numbers can map to the same person. You can rename or re-match a
  speaker at any time by clicking their name in the note."
- Implemented with `Adw.Dialog` and model-bound `Adw.ActionRow`s: playback button on the
  left, colored bold name and dimmed quoted sample in the middle, and a colored selection
  menu with check marks on the right, matching the HTML row order. The candidate heading
  is “Existing Speakers” because more than two recordings and named old identities from
  re-transcription are supported. Menus include “Keep as New Speaker”.
- New labels default to “New Speaker”; Apply confirms every choice, including keeping
  independent identities. Cancel writes nothing. The banner shows the first recording
  needing a decision and advances when its choices are confirmed.
- Samples use the first nonempty turn with reliable timing, capped at ten seconds and
  bounded by the next timestamp or recording end when needed. Without reliable timing,
  the quote remains visible, playback is disabled, and manual matching is available.
- Dialogs scroll at narrow widths. Playing a sample stops the previous playback; clicking
  it again stops. Closing the dialog stops playback.

## 5. Dialog: Re-transcribe (≈480×520)

- Header: "Cancel" · title "Re-transcribe" · suggested "Transcribe".
- Intro: "Re-transcribe "Recording 1" (0:58) from the kept original audio. Timestamps
  or speaker detection that were off during transcription can be added this way."
- Group: "Model" row (value "Deepgram · nova-3", opens model picker), switches
  "Timestamps" and "Speaker Detection" (disabled when unsupported).
- Warning: "The text of this recording will be replaced, including any manual edits.
  Other recordings in the note are not affected. Speaker names are kept where possible."

M6 implementation behavior:
- The picker starts with the bottom bar's current model only when it supports files.
  Live-only models require an explicit file-model choice. Switches initially reflect
  the current transcript and use the selected model's effective batch capabilities.
- Only a fully successful transcription replaces this recording. Failed/cancelled work
  keeps its previous text, identities, original audio and completion state. Progress and
  Cancel use the existing bottom bar; conflicting recording/import/edit actions are disabled.
  Cancel is disabled during the final atomic save; closing waits for that save to finish.
- Names are preserved only with strong time-overlap evidence (80% coverage and twice the
  runner-up score). Uncertain identities require manual matching. Provider numbering is
  never treated as evidence of identity, and unmatched people remain unmatched on retry.
- The warning uses theme colors, the header contains Cancel/Transcribe, and content scrolls
  so the 480×520 dialog also works at 360 px.

## 6. Preferences — Providers (≈640×600)

- `Adw.PreferencesDialog` with pages "General" and "Providers". General follows the
  same standard grouped-row styling; the HTML reference does not specify its contents.
- Providers page: group "Providers", description "Transcription happens in the cloud.
  A provider can offer several models.", header button "Add Provider".
- Rows (navigate to detail): avatar initial + name + subtitle "<API type> API · N
  model(s)", e.g. "Deepgram — Deepgram API · 1 model", "OpenAI — OpenAI API · 2
  models", "Groq — OpenAI-compatible API · 1 model".
- **Implemented** (`ui/preferences.blp`), including the General page below.

## 7. Preferences — Provider detail (≈640×780)

Pushed as a subpage (back button) titled with the provider name.
- Group "Connection": entry "Name"; combo "API Type" (Deepgram / OpenAI /
  OpenAI-compatible / OpenRouter / Gemini AI Studio); entry "Base URL" (e.g. `https://api.groq.com/openai/v1`);
  password entry "API Key" with *Show Key* toggle. Description: "The API type decides
  the request format. Most providers offer an "OpenAI-compatible" API."
- Group "Models", description "What each model can do is set when you add it.", header
  button "Add Model". Rows: model name + capability tags, *Edit Model* button.
- Destructive button "Remove Provider" at the bottom.
- No Save button: changes to an existing provider apply when a field is confirmed
  (Enter or the row's apply button) or the API type changes. Adding a new provider uses
  an "Add" button in the header.
- Widgets: `Adw.NavigationPage` inside the dialog, `Adw.EntryRow`,
  `Adw.PasswordEntryRow`, `Adw.ComboRow`, `Adw.ButtonRow`.

## 8. Dialog: Add Model (≈480×600)

- Header: "Cancel" · title "Add Model" · suggested "Add".
- Entries: "Model ID", "Display Name".
- Group "This Model Supports" (check rows):
  - "Realtime Transcription" — "Text appears while recording. If unchecked, the whole
    recording is uploaded after it stops."
  - "Timestamps" — "Each segment carries a time. Needed for subtitle export."
  - "Speaker Detection" — "Tells different people apart and labels them."
- Footnote: "Different models from the same provider can differ. Check the provider's
  documentation. Unchecked options are shown as unavailable when recording."
- Known endpoint restrictions disable unsupported capability checkboxes. Live-only
  model IDs force Realtime Transcription and disable file operations. Provider/model
  summaries and bottom-bar hints use the same effective capability rules.

## Visual notes

- Fonts in the mock: Cantarell for UI, Source Code Pro for timestamps. In GTK, use the
  system font and the `monospace`/`numeric` style classes instead of hardcoding fonts.
- Accent blue `#1c71d8`, destructive red: use libadwaita's accent/destructive colors,
  not literal values.
- All popovers and dialogs close on Escape and on outside click (stock behavior).


## 9. Preferences — General (M7)

- "Transcription" group: Default Model combo (provider and model name), then Transcription
  Language entry with an apply button. Empty means automatic detection; the tooltip gives
  `zh`, `en` and `ja` examples. Invalid codes show an error state and are not saved.
- "Storage" group: Data Directory row showing the current path; activating opens the
  directory. This release does not move the library or change its location.
- "Troubleshooting" group: Log Directory row opening local diagnostics. The description
  states that logs stay on this device and users can attach them when reporting problems.
- Most recently selected model takes priority on restart, followed by the default model,
  then the first configured model. Missing models fall back in that order. Changing the
  default selects it when idle; running tasks keep the model/language captured at start.
- Interface language follows the system; English and Simplified Chinese (`zh_CN`) are
  included. This is separate from the transcription language hint.

## 10. Keyboard and accessibility (M7)

The main menu offers Keyboard Shortcuts. New Note uses Ctrl+N, Search Notes Ctrl+F,
Import Audio Ctrl+O, Rename Note F2, Preferences Ctrl+comma, shortcut help Ctrl+?, and
Quit Ctrl+Q (Command replaces Ctrl on macOS). Escape closes search; it never stops a
recording. Shortcuts share action availability with buttons, including busy-state guards.
Search is disabled while capturing/transcribing so it cannot hide narrow-window Stop
controls. Import from the sidebar opens the content pane to show progress and Cancel.

Icon-only application controls have translated accessibility labels; pause/resume and
play/stop labels change with their actions. Dialogs remain keyboard navigable and
scroll at 360 px. Visible error states have explanatory text in addition to color.
