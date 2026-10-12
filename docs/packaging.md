# Packaging plan

Nothing here is automated yet. This records the intended approach so code stays
compatible with it.

## Shared build steps

1. `uv run scripts/build_ui.py` — `.blp` → `.ui` (inside the package).
2. `uv run scripts/i18n.py compile` — `.po` → `.mo` (inside the package).
3. Build a wheel: `uv build`. Generated `.ui`/`.mo` are included via hatch `artifacts`.

Because UI files, icons, CSS and translations live inside `src/kikoe/`, every packager
only has to ship the Python package, GTK/libadwaita, ffmpeg, and PortAudio.
Microphone capture uses sounddevice/CFFI; NumPy and libsndfile are not required.

## Linux — Flatpak (primary)

- Runtime: `org.gnome.Platform` (the first version whose Python is ≥ 3.14).
- Python deps: generate a module with `flatpak-pip-generator` from `uv export`.
- Include PortAudio built with an audio backend compatible with the runtime audio socket.
  The final manifest and sandbox microphone test remain part of distribution work.
- Install `data/*.desktop`, `data/*.metainfo.xml` and the icon under
  `/app/share/...` using the app ID `io.github.first_storm.Kikoe`.
- ffmpeg: from the runtime / `org.freedesktop.Platform.ffmpeg-full` extension.
- Permissions to request: `--socket=wayland`, `--socket=fallback-x11`,
  `--share=ipc`, `--device=dri`, `--socket=pulseaudio` (mic), `--share=network`.
  Use the file chooser portal for import/export instead of filesystem access.

## macOS — PyInstaller `.app`

- GTK, libadwaita, PyGObject from Homebrew; freeze with PyInstaller (it has hooks for
  `gi`). Gaphor's build is a good reference.
- Bundle a static `ffmpeg` next to the executable; `kikoe.media.ffmpeg` finds it there.
- Microphone access requires `NSMicrophoneUsageDescription` in `Info.plist`, for example
  "Kikoe records your microphone to turn speech into text notes." Verify the system
  permission prompt, denial and subsequent permission changes with the packaged app.
- Bundle the PortAudio library used by sounddevice; verify PyInstaller includes the
  platform library from the wheel or Homebrew installation.
- Sign and notarize for distribution.

## Windows — MSYS2 + PyInstaller

- Build in the MSYS2 UCRT64 environment (GTK, libadwaita, PyGObject from pacman) and
  freeze with PyInstaller; or use gvsbuild for an MSVC toolchain.
- Bundle `ffmpeg.exe` next to `kikoe.exe` and sounddevice's PortAudio DLL (or the MSYS2
  PortAudio library when building there). Verify Windows microphone privacy settings.
- Python gettext translates `.ui` templates into the user cache before GTK loads them,
  including on systems without `locale.bindtextdomain`. Bundle the compiled `zh_CN`
  catalog and verify both template and Python labels in the packaged app.
- Installer: Inno Setup or MSIX.

## Cross-platform pitfalls to avoid in code

- Paths: only `pathlib` and `kikoe.paths`; never assume `/` or `~/.config`.
- Subprocess: list arguments, no shell, find binaries via helpers (`find_ffmpeg`).
- No Linux-only libraries (D-Bus, GSettings, libsecret direct use, PulseAudio APIs)
  outside an explicitly platform-guarded module.
- File names: avoid characters invalid on Windows (`<>:"/\|?*`) when deriving names
  from note titles.

## M4 microphone verification

Linux was verified with a default input device, native-rate mono int16 capture, pause/
resume, FLAC finalization and conversion to the existing Opus upload format. The GTK
recording screen is checked at 1180×780 and 360 px. macOS and Windows are **not yet
verified on real hardware**; the M4 acceptance boundary is Linux first.

On each additional platform, start with a configured OpenAI-compatible batch model:

1. Record into a new note; confirm the timer and level meter respond to actual input.
2. Pause for several seconds, resume, and stop. The saved duration excludes the pause.
3. Export Original Audio and play the FLAC; check it contains both recorded intervals.
4. Deny microphone access/remove the default device; confirm an actionable error appears.
5. Fail or cancel transcription; audio survives and Retry Transcription updates the same
   recording. Close while recording and verify retained audio on the next launch.
6. Interrupt the process while capturing; launch again and choose Recover. No audio is
   uploaded until Retry Transcription is explicitly selected.

Do not claim these platform checks passed until the packaged app is tested there.

## M5 verification

The official SDKs resolve on Python 3.14. Linux GTK recording layouts were inspected
at 1180×780 and 360 px in light/dark mode, including the inline preview caret and
controls with animations disabled. SDK requests and live streams are covered with fake
responses; ffmpeg resampling is checked at 16 and 24 kHz. No real provider account was
used, and macOS/Windows realtime audio have not been verified on hardware.

For real-account acceptance, test each configured hosted provider's file model, and
Deepgram/OpenAI/Gemini live models: spoken text, multiple turns, pause/resume, a network
interruption and catch-up, stop during catch-up, and original FLAC export. For Deepgram,
verify speaker turns; for Gemini, include a recording longer than ten minutes. A live-only
model must require an explicit file-model choice for retry. Closing and startup recovery
must preserve audio and confirmed text without automatically uploading them.

## M6 playback verification

Linux default-output smoke checks completed for a full generated recording and a bounded
sample, including normal output-stream cleanup. GTK main window and dialogs were inspected
at 1180×780 and 360 px in light/dark themes; matching Apply and subsequent separation
were also exercised against an isolated temporary library. No real provider API was used.

Playback reuses the packaged ffmpeg and sounddevice/PortAudio dependencies. The default
output device must support mono int16 at its advertised native sample rate. Tests use
fake output devices and retained temporary WAV files; they cover seek bounds, cancellation,
decoder failure and resource cleanup without accessing real audio hardware.

On each packaged platform, play a retained recording and a timestamped speaker sample,
switch playback targets, stop, then begin microphone capture. Verify only one output
stream is active and no decoder remains after closing the dialog/window. Missing output
devices, missing original audio and audio permission failures must produce a user-readable
message. macOS/Windows hardware verification remains part of M8.


## M7 release preparation and verification

The application is licensed under GPL-3.0-or-later; AppStream metadata remains CC0-1.0.
Ship LICENSE and the full-color and symbolic icons. The UI now requires libadwaita 1.8
(for Adw.ShortcutLabel). Build templates and catalogs before building a wheel:

```sh
uv run scripts/build_ui.py
uv run scripts/i18n.py compile
uv build
appstreamcli validate --no-net data/io.github.first_storm.Kikoe.metainfo.xml
```

Release screenshots under `data/screenshots/` use only synthetic content. AppStream image
URLs point at these files on the repository's main branch; after pushing the release,
verify those URLs resolve before submitting metadata to a distributor. Local `--no-net`
validation cannot establish remote availability.

Linux GTK checks cover 1180×780 and 360 px, English and Simplified Chinese, light/dark
mode, search/empty results/clear, persistent defaults, language validation, shortcut
registration and retranscription language propagation. Existing speaker and retranscription
dialogs are also inspected at 360 px. No provider accounts or actual microphone are
used for M7. Unit tests simulate missing native gettext bindings; real macOS/Windows
packaged startup remains an M8 requirement.

Logs are local and bounded (2 MiB plus three backups). Opening a data/log directory
uses Gtk.FileLauncher, allowing the platform's launcher/portal to handle it. A write
failure in the log directory leaves terminal diagnostics available. Changing/migrating
the data directory and automatic error uploads are not implemented.
