# Packaging

Every package is built by GitHub Actions from the same wheel. Pull requests that touch
packaging run the matching workflow; pushing a `v*` tag runs all of them through
`release.yml` and attaches the results to a **draft** release for review.

| Platform | Format | Architectures | Workflow |
| --- | --- | --- | --- |
| Linux | Flatpak bundle (`.flatpak`) | x86_64, aarch64 | `package-linux.yml` |
| Linux | AppImage | x86_64, aarch64 | `package-linux.yml` |
| macOS 15+ | `.dmg` with `Kikoe.app` | arm64, x86_64 | `package-macos.yml` |
| Windows 10/11 | Inno Setup installer, portable `.zip` | x64, arm64 | `package-windows.yml` |

Each architecture is built natively (`ubuntu-24.04-arm`, `windows-11-arm`,
`macos-15-intel` runners); nothing is cross-compiled or emulated. In a private
repository, macOS minutes count ten times and Windows minutes twice against the
Actions allowance, so the workflows only run for changes under their own paths.

## Shared steps

1. `wheel.yml` (Ubuntu 26.04, like CI) compiles Blueprints and translations, builds
   the wheel with `uv build` (generated `.ui`/`.mo` files are included via hatch
   `artifacts`) and exports the locked runtime requirements to `requirements.txt`.
2. `packaging/ffmpeg/build.sh` builds the bundled ffmpeg (AppImage, macOS, Windows):
   ffmpeg (the `FFmpeg/FFmpeg` GitHub tag archive, since ffmpeg.org often fails to
   resolve on CI runners) and libopus from pinned, hash-checked sources, linked statically.
   `packaging/ffmpeg/configure.sh` keeps all audio decoders, all demuxers and filters,
   but only the libopus/FLAC/PCM encoders, the Ogg/FLAC/WAV/PCM/null muxers and the
   file/pipe protocols; the Flatpak manifest uses the same script. The result is
   cached per architecture. Update `FFMPEG_VERSION`/`OPUS_VERSION` and their hashes
   together (and the Flatpak manifest's ffmpeg `tag` and `commit`).
3. The AppImage, macOS and Windows builds freeze the wheel with PyInstaller using
   `packaging/pyinstaller/kikoe.spec` as a one-folder app. ffmpeg goes next to the
   executable, where `kikoe.media.ffmpeg` looks for it. `rthook_portaudio.py` points
   sounddevice at the bundled PortAudio.
4. Every job starts the packaged app and checks that it is still running after
   20 seconds (the Flatpak job imports the app instead, as it has no display).

## Linux — Flatpak

- Manifest: `packaging/flatpak/io.github.first_storm.Kikoe.yml`, runtime
  `org.gnome.Platform//51`, the first with Python 3.14 (Freedesktop SDK 26.08).
- Python dependencies: `packaging/flatpak/python3-dependencies.json` is generated from
  `uv.lock` by `uv run scripts/flatpak_deps.py`; CI fails when it is out of date. It
  lists the locked wheels (per architecture for compiled ones) and builds PyGObject and
  pycairo with Meson from their locked source releases.
- PortAudio 19.7 (ALSA, which the runtime routes to PulseAudio/PipeWire) and ffmpeg are
  built as modules. The Kikoe module installs the CI-built wheel from `dist/`, so the
  manifest is not yet suitable for Flathub, which builds from source; that would need
  a blueprint-compiler module and a source build of the wheel.
- Permissions: `--socket=wayland`, `--socket=fallback-x11`, `--share=ipc`,
  `--device=dri`, `--socket=pulseaudio` (microphone and playback), `--share=network`,
  and `--talk-name=org.freedesktop.secrets` for python-keyring's Secret Service backend.
  Import/export use the file chooser portal.
- Install a bundle with `flatpak install --user Kikoe-<version>-linux-<arch>.flatpak`;
  the GNOME runtime comes from Flathub.

## Linux — AppImage

- `packaging/linux/build-appimage.sh`, run in an `ubuntu:26.04` container: the oldest
  Ubuntu with libadwaita ≥ 1.8. The AppImage bundles GTK, libadwaita, PortAudio and
  Python, but not glibc, so it needs a distribution at least as new as Ubuntu 26.04
  (glibc of that release). Older systems should use the Flatpak.
- ALSA (`libasound`) is deliberately taken from the host so its PipeWire/PulseAudio
  plugins and configuration work.
- Run directly (`chmod +x`); FUSE is needed unless `APPIMAGE_EXTRACT_AND_RUN=1` is set.

## macOS — `.app` in a disk image

- `packaging/macos/build.sh`: GTK, libadwaita and the Adwaita icons from Homebrew,
  Python 3.14 from uv; PyInstaller collects the Homebrew libraries into the bundle.
- Homebrew's libraries require the macOS version of the build runner, so the app needs
  macOS 15 or newer (`LSMinimumSystemVersion`).
- `Info.plist` contains `NSMicrophoneUsageDescription` ("Kikoe records your microphone
  to turn speech into text notes.").
- The app is ad-hoc signed only. Until it is signed with a Developer ID and notarized,
  users must right-click → Open the first time, or run
  `xattr -dr com.apple.quarantine /Applications/Kikoe.app`.

## Windows — installer and portable zip

- `packaging/windows/build.sh` runs in MSYS2: UCRT64 for x64, CLANGARM64 for arm64.
  PyPI has no wheels for MSYS2's Python, so compiled dependencies MSYS2 packages (cffi,
  cryptography, pydantic, pycairo) come from pacman, and
  `packaging/windows/missing_requirements.py` keeps only the rest of
  `requirements.txt` for pip, which builds PyGObject (and jiter, if MSYS2 lacks it)
  from source. Versions of the pacman packages may differ slightly from `uv.lock`;
  `pip check` reports conflicts in the log.
- The one-folder app is zipped as a portable build, and `packaging/windows/kikoe.iss`
  turns it into a per-user (or, on request, per-machine) Inno Setup installer. The x64
  installer also runs on Windows on Arm through emulation; the arm64 one is native.
- The executables are not code-signed, so SmartScreen warns on first start.
- Python gettext translates `.ui` templates into the user cache before GTK loads them,
  including on systems without `locale.bindtextdomain`, so all 14 bundled catalogs
  cover both template and Python labels. Chinese catalogs use `zh_Hans` and `zh_Hant`;
  the loader also accepts regional system locales. PyInstaller collects GTK's own
  translated labels for the supported languages using GTK's regional catalog names.

## Not done yet

- Developer ID signing and notarization (macOS) and Authenticode signing (Windows)
  need certificates stored as repository secrets.
- Flathub submission (see the Flatpak notes above).
- Microphone and playback acceptance on real hardware for each package (see the M4–M6
  checklists below); CI only checks that the app starts.

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

Mistral's file requests and streams are covered with fake HTTP responses, and its realtime
client runs against a loopback WebSocket server with the real SDK. No Mistral account was
used, so these assumptions need a real check: the response shape for word timestamps
(treated as one segment per word), whether diarization requires segment granularity
(assumed), the 60 minute and 500 MB limits (the docs disagree on duration), event order
in streams (text may arrive before or after its segment), and flush behavior in realtime
sessions (quiet-period heuristic).

For real-account acceptance, test each configured hosted provider's file model, and
Deepgram/OpenAI/Gemini/Mistral live models: spoken text, multiple turns, pause/resume, a network
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
(for Adw.ShortcutsDialog). Build templates and catalogs before building a wheel:

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

Logs are local and bounded (2 MiB plus three backups). Opening a data/log folder
uses Gtk.FileLauncher, allowing the platform's launcher/portal to handle it. A write
failure in the log directory leaves terminal diagnostics available. Changing/migrating
the data folder and automatic error uploads are not implemented.
