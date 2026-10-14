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
  above the timestamp (small tabular digits, dimmed), and the text on the right in slightly
  larger type. The left column disappears when both timestamps and speakers are hidden.
- Bottom bar (below the content pane only, libadwaita `toolbar` style, flat buttons):
  model button with a cloud icon, the model name in bold and the provider dimmed; then
  right-aligned "Import Audio…" (folder icon) and the primary "Append Recording"
  (`suggested-action`, mic icon). No pill buttons and no custom chip styling.
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
- **Model capabilities**: declared per mode. *File transcription* (timestamps, word
  timestamps, speaker diarization, streaming preview) and *realtime transcription* (text
  appears while recording; timestamps, speaker diarization). A bundled preset catalog
  fills these in for known models and marks what an endpoint rejects; the user can change
  everything else, and the UI disables what a model cannot do.

## 1. Main window — note with appended recording

Default size 1180×780. Two panes.

**Sidebar** (≈260 px, "Notes"):
- Header: app title "Kikoe"; buttons *New Note*, *Search Notes*, *Main Menu*.
- When the window controls are at the start (macOS, or a left button layout on Linux),
  *New Note* joins Search and Main Menu at the end and the sidebar title is hidden,
  so the app's buttons never crowd the window controls.
- List rows: note title + subtitle with date and length, e.g.
  - "Podcast Topics" — "Today 15:12 · 2 recordings"
  - "Group Meeting" — "Yesterday · 42:10"
  - "Interview Material" — "Oct 9 · Imported audio · 26:31"
  - "Voice Memo" — "Oct 8 · 0:47"

**Content header**: note title ("Podcast Topics") with subtitle "2 recordings · 1:39".
Header buttons: *Undo* and *Redo* at the start (icon buttons, transcript edits only);
at the end:
- *Display Options* (menu): check items "Show Timestamps" and "Show Speakers". They
  only change how this note is shown and exported; the transcript is not modified.
- *Note Menu*: "Rename…", "Copy All Text", "Export…", "Delete Note" (destructive).
  Export is a menu item rather than its own header button so the header still fits
  at 360 px with Undo and Redo.

**Banner** (when an appended recording has unmatched speakers):
"Recording 2 was appended and its speakers are not yet matched with Recording 1" with
button "Match Speakers…" → opens screen 4. Widgets: `Adw.Banner`.

**Recordings**, each as a section:
- Header, two lines: "Recording 1" (heading) with flat circular *Play This Recording*
  and *Recording Menu* buttons, then the dimmed meta line "Today 14:30 · 0:58 ·
  Deepgram nova-3 · Timestamps, Speakers"; appended ones say "Today 15:12 appended ·
  0:41 · …". No separator; spacing separates recordings. *Recording Menu*:
  "Re-transcribe…", "Export Original Audio…", "Match Speakers…", "Delete Recording".
- Segments: a meta column (speaker name, colored, clickable → "Rename Speaker"; below
  it the time range, tabular digits) and the text. Speaker/timestamp columns hide per
  display options. Below 520 sp the meta column moves above the text on one line.
- The transcript is edited in place, like a document: segment text is a borderless
  text view that tints on hover and, while it holds the caret, with the accent color.
  There is no edit button or dialog. Edits save themselves after a short pause in
  typing and whenever the caret leaves the segment; empty text is not saved (toast
  "Segment text cannot be empty", the text reverts).
  - Enter moves to the next segment, Shift+Enter inserts a line break, Escape reverts
    typing not saved yet, Up/Down on the first/last line move between segments. While
    an input method is composing, these keys belong to it.
  - The time range is a flat button ("Add Time" when a segment has none) opening a
    compact popover with *Start* and *End* labels beside standard entry fields.
    Labels have mnemonics; Tab moves from Start to End. The hint explains MM:SS,
    H:MM:SS and optional decimal seconds (for example, 00:26.860); empty clears a time.
    Valid changes apply when it closes or on Enter, including Escape/outside click.
    Invalid fields show an error style and a specific explanation; Enter keeps the
    popover open and focuses the first invalid field. Closing with invalid input
    restores the saved times and shows a toast explaining that changes were not saved.
  - Undo (Ctrl+Z) and Redo (Ctrl+Shift+Z, Ctrl+Y) cover text and time edits across
    the whole note; quick successive saves of one segment are one step. History is
    cleared when another note opens or recordings are added, removed or re-transcribed.
    Undo/Redo scroll to the segment and put the caret in it.
  - While recording, importing or recovering, the text is read-only and Undo/Redo are
    disabled. Live rows from realtime transcription are never editable.

**Bottom bar** (recording controls):
- *Transcription Model* button showing "nova-3 · Deepgram" (`win.choose-model`) opens
  the *Transcription Model* dialog (`Adw.Dialog`, 440 px wide, changes apply at once):
  - One group per provider, titled with its name: one radio row per model, title = model
    name, subtitle = capability summary ("Realtime · Timestamps · Speakers", or "Text
    only"). Initial focus is on the current model.
  - Group "Options", description "Transcribes while recording" or "Transcribes after
    recording": switch rows "Timestamps" and "Speakers" (whether the next recording
    requests them). For models with word timestamps, while Timestamps is on, a combo row
    "Timestamp Detail" offers "Segments", "Words", "Segments and Words". If the model
    lacks a capability, its switch is disabled with the subtitle "Not supported by
    this model"; the earlier choice comes back with a model that supports it.
    When a transcription language is set and the next upload would drop it (Mistral
    cannot combine a language with timestamps or speakers), a dimmed caption under the
    rows says the language is detected automatically; it shows only for uploads and
    disappears when both options are off.
  - Button row "Manage Providers and Models…" closes the dialog and opens Preferences.
  The button's tooltip repeats the model, provider and the hint, including "· This
  model does not support <list>" when relevant.
- Buttons: "Import Audio…" and the primary "Append Recording" (on an empty note,
  "Record").
- The bar is always a single line and keeps the same height whenever it is shown.
  It is hidden when no note is selected; it appears once a note is opened. As its own
  width shrinks (breakpoints on the bar, not the window): below 600 sp the provider name
  hides and Import Audio becomes icon-only (tooltip and accessible name kept). The
  primary Record/Append Recording button always keeps its label.

**Implementation status**:
- Implemented: sidebar list with *New Note* (disabled until a provider exists);
  content header with title and subtitle; *Display Options*; *Note Menu* with "Rename…",
  "Copy All Text" and "Delete Note"; *Export* with an Adw.Dialog choosing format and
  options (timestamps, speakers), followed by a save dialog, with subtitle options
  disabled when the note has no timestamps; recordings and segments
  with speaker colors (unnamed speakers show as "Speaker N"); recording header menu
  with "Export Original Audio…"; the bottom bar’s model dialog with its Timestamps/Speakers
  options (they set what the next transcription requests) and hint text, and "Import Audio…", which
  adds to the open note or creates one named after the file, with progress, Cancel and a
  failure dialog; Record/Append Recording, pause/resume, Stop, live level meter, batch
  transcription after stopping, retained-audio retry, and startup recovery.
- M6 implemented: unmatched-speaker banner; recording playback/stop; clickable colored
  speaker names with Rename Speaker and Match Speakers; reversible many-to-one matching
  with quoted samples and playback; Re-transcribe with an independent model/options picker.
- M7 implemented: Search Notes opens a sidebar search bar; title and transcript matches
  are case-insensitive literal substrings. Empty queries restore all notes; no matches
  show "No Matching Notes". Filtering preserves the open note. Escape closes search.
  The sidebar is approximately 260 px at desktop sizes; search precedes the main menu.
- At 360 px the idle bar stays on one line: model name, icon-only Import Audio, and the
  labeled primary button. Model and import tooltips retain their explanations. Import
  Audio is also available in the main menu without a selected note.
- Models with the Streaming Preview capability display text while transcribing imports, stopped recordings,
  retries and re-transcriptions. Imports append a temporary recording section; imports
  without an open note show a temporary page titled after the file, with progress and Cancel.
  No note or sidebar entry is created until the import succeeds. Retry and Re-transcribe
  preview new text in the original recording's position, keeping neighboring recordings.
  Completed segments use normal text; unfinished text is dimmed with the existing blinking
  caret. Temporary rows cannot be edited, played or used to match speakers. Scrolling follows
  the bottom only when the reader was already there. Cancel or failure removes previews and
  restores existing text; success replaces it with the saved result. Cancel is disabled
  during the final save. Whisper and compatible APIs retain their existing file requests.
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
  Deepgram, OpenAI, Gemini and Mistral native live endpoints use the realtime branch.
- Bottom bar follows the HTML's order: 10 px red recording indicator, elapsed time
  `00:31` (tabular digits), 28 live level bars (3 px wide, up to 28 px high),
  model/options summary, round icon-only "Pause Recording" button, and a
  `suggested-action` "Stop" button with stop icon (Stop saves, so it is not styled
  destructive). The normal model/import/record controls are replaced.
- Pause changes the central heading and header/sidebar subtitle to "Paused", freezes
  the timer, clears the meter, and switches the pause control to "Resume Recording".
  The red indicator dims while paused. Resume appends audio to the same recording.
- The recording bar is always one line. Below 600 sp the summary hides; below 480 sp
  the meter hides too, so the timer, pause/resume and the labeled Stop fit at 360 px. Below 480 sp, the content
  header keeps only Close, on the side the system puts window controls, so all app
  controls still fit. Colors
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

- Sidebar empty state: compact status page "No Notes Yet" (icon `document-edit-symbolic`;
  "No Matching Notes" with `edit-find-symbolic` while searching).
- Content: status page with app icon, title "Welcome to Kikoe", description "Kikoe
  turns speech into text using cloud services. Add a provider and a model first, then
  record or import audio.", suggested pill button "Add Provider".
- **Implemented** (`ui/window.blp`).

## 3b. No note selected (providers exist)

- Content: status page with the symbolic `document-edit-symbolic` icon (the app icon is
  reserved for the welcome page), title "No Note Selected", description "Create
  a note, then record or import audio into it.", suggested pill button "New Note"
  (creates a blank note, same as the sidebar button and `Ctrl+N`).
- The bottom bar is hidden in this state; it appears once a note is opened.

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
- Group: "Model" row (value "Deepgram · nova-3", opens model picker), then the same
  "Options" group as the Transcription Model dialog ("Timestamps", "Timestamp Detail",
  "Speakers"), using the selected model's file capabilities.
- Warning: "The text of this recording will be replaced, including any manual edits.
  Other recordings in the note are not affected. Speaker names are kept where possible."

M6 implementation behavior:
- The picker starts with the bottom bar's current model only when it supports files.
  Live-only models require an explicit file-model choice. Switches initially reflect
  the current transcript and use the selected model's file capabilities.
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
  models", "Groq — Groq API · 1 model".
- **Implemented** (`ui/preferences.blp`), including the General page below.

## 7. Preferences — Provider detail (≈640×780)

Pushed as a subpage (back button) titled with the provider name.
- Group "Connection": entry "Name"; combo "API Type" (Deepgram / OpenAI /
  OpenAI-compatible / OpenRouter / Gemini AI Studio / MiMo / Groq); entry "Base URL" (e.g. `https://api.groq.com`);
  password entry "API Key" with *Show Key* toggle. Description: "The API type decides
  the request format. Most providers offer an "OpenAI-compatible" API."
- Group "Models", description "What each model can do is set when you add it.", header
  button "Add Model". Rows: model name + capability tags, *Edit Model* button.
- Group "File Transcription" before Models: SpinRows for Parallel Requests (1–32,
  default 2), Chunk Length (Minutes), and Chunk Size (MB), plus an Audio Format ComboRow
  and Reset Transcription Defaults ButtonRow. Limits and defaults depend on the API;
  Gemini additionally enforces its 30-minute metadata limit at request time. See
  [Provider upload settings](upload-settings.md) for official sources and exact defaults.
  Rows apply immediately; content scrolls at 360 px and inherits Adwaita styling.
- Destructive button "Remove Provider" at the bottom.
- No Save button: changes to an existing provider apply when a field is confirmed
  (Enter or the row's apply button) or the API type changes. Adding a new provider uses
  an "Add" button in the header.
- Widgets: `Adw.NavigationPage` inside the dialog, `Adw.EntryRow`,
  `Adw.PasswordEntryRow`, `Adw.ComboRow`, `Adw.ButtonRow`.

## 8. Dialog: Add Model (≈480×600)

- Header: "Cancel" · title "Add Model" · suggested "Add".
- Entries: "Model ID", "Display Name".
- Group "What This Model Supports", description "Known models are filled in
  automatically. Options the provider rejects for this model are unavailable.":
  - Expander row with switch "Transcribe Files" — "Imported files, and recordings
    uploaded after they stop." Check rows: "Timestamps", "Word Timestamps", "Speaker
    Detection", "Streaming Preview" ("Text appears while a file is transcribed.").
  - Expander row with switch "Transcribe While Recording" — "Text appears while
    recording. If off, the whole recording is uploaded after it stops." Check rows:
    "Timestamps", "Speaker Detection".
- Typing a model ID applies the preset from `storage/model_presets.json` until the user
  changes an option; editing never replaces saved options. Options the preset marks
  unsupported are disabled with the tooltip "This model does not support this option.";
  a mode the model lacks is disabled, and the other mode then has no switch (always on).
  Saving with both modes off shows "Turn on file or realtime transcription."
- Provider/model summaries and bottom-bar hints use the same stored capabilities.

## Visual notes

- Fonts in the mock: Cantarell for UI, Source Code Pro for timestamps. In GTK, use the
  system font and the `numeric` style class (tabular digits) instead of hardcoding fonts.
- Accent blue `#1c71d8`, destructive red: use libadwaita's accent/destructive colors,
  not literal values.
- All popovers and dialogs close on Escape and on outside click (stock behavior).


## 9. Preferences — General (M7)

- "Transcription" group: Default Model combo (provider and model name), then Transcription
  Language entry with an apply button. Empty means automatic detection; the group description gives
  `zh`, `en` and `ja` examples (not a tooltip, so touch users see it). Invalid codes show an error state and are not saved.
- "Audio" group: Microphone (used for new recordings) and Audio Output (used for playback)
  combo rows. The first item is "System Default", which is also the initial choice. The
  choice is saved by device name; a saved device that is unplugged stays listed as
  "<name> (Unavailable)" and the system default is used until it returns. A running
  recording keeps the device it started with.
- "Storage" group: Data Folder row showing the current path; activating opens the
  folder. This release does not move the library or change its location.
- "Troubleshooting" group: Log Folder row opening local diagnostics. The description
  states that logs stay on this device and users can attach them when reporting problems.
- Most recently selected model takes priority on restart, followed by the default model,
  then the first configured model. Missing models fall back in that order. Changing the
  default selects it when idle; running tasks keep the model/language captured at start.
- Interface language follows the system; English and Simplified Chinese (`zh_CN`) are
  included. This is separate from the transcription language hint.

## 10. Keyboard and accessibility (M7)

The main menu offers Keyboard Shortcuts. New Note uses Ctrl+N, Search Notes Ctrl+F,
Import Audio Ctrl+O, Rename Note F2, Export Note Ctrl+Shift+E, Undo Ctrl+Z, Redo
Ctrl+Shift+Z/Ctrl+Y, Preferences Ctrl+comma, shortcut help Ctrl+?, and
Quit Ctrl+Q (Command replaces Ctrl on macOS). Escape closes search; it never stops a
recording. Shortcuts share action availability with buttons, including busy-state guards.
Search is disabled while capturing/transcribing so it cannot hide narrow-window Stop
controls. Import from the sidebar opens the content pane to show progress and Cancel.

Icon-only application controls have translated accessibility labels; pause/resume and
play/stop labels change with their actions. Dialogs remain keyboard navigable and
scroll at 360 px. Visible error states have explanatory text in addition to color.

HIG audit follow-ups:
- Search Notes is a toggle button bound to the search bar (no separate close button), and
  typing in the notes list starts a search unless a capture or import is running.
- Export is an `Adw.Dialog` choosing from four formats with options for timestamps and
  speaker labels before opening a save dialog (hence the ellipsis); subtitle formats are
  disabled when the note has no timestamps.
- Keyboard Shortcuts is an `Adw.ShortcutsDialog`; Ctrl+W closes the window (the same
  save-then-close path as Quit).
- Secondary click or long press on a sidebar note offers Rename… and Delete Note without
  opening the note. The target row stays highlighted while its context menu is open,
  regardless of pointer position; closing the menu restores its normal hover/selection
  appearance without changing the selected note.
- Rename Speaker and Add/Edit Model ask “Discard Changes?” when closed with
  Escape or a click outside while they hold unsaved input; Cancel discards immediately.
- Failures the user must read (transcription, re-transcription, capture) use a dialog;
  small self-explanatory ones (a missing folder or file, playback problems) use a toast.
  Toast text stays short; details go in dialogs.
- The window size and maximized state are remembered between launches.
