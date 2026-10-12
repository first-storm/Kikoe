# Desktop packaging

`.github/workflows/package.yml` builds native packages on every PR, main push and
`v*` tag, and can be started manually from Actions. Download each platform's artifacts
from the **Desktop packages** workflow. These are review builds, not signed releases.

| Platform | Architecture | Runner / native stack | Outputs |
| --- | --- | --- | --- |
| macOS | x86_64 | macos-15-intel / Homebrew | `.app` in ZIP, DMG |
| macOS | ARM64 | macos-15 / Homebrew | `.app` in ZIP, DMG |
| Windows | x86_64 | windows-2022 / MSYS2 UCRT64 | portable ZIP, Inno Setup installer |
| Windows | ARM64 | windows-11-arm / MSYS2 CLANGARM64 | portable ZIP, Inno Setup installer |
| Linux | x86_64 | ubuntu-24.04 / Ubuntu 26.04 container | AppImage, Flatpak, tar.gz |
| Linux | ARM64 | ubuntu-24.04-arm / Ubuntu 26.04 container | AppImage, Flatpak, tar.gz |

Here x86 means 64-bit x86, and ARM means ARM64 (`aarch64`). 32-bit x86 and ARMv7
are not supported. AppImages and portable Linux archives require a host compatible
with Ubuntu 26.04's glibc; Flatpak uses the GNOME 50 runtime for a consistent base.
macOS builds target macOS 14 or newer. Windows builds target Windows 10/11 on x64
and Windows 11 on ARM64; the ARM package contains native ARM binaries.

## Shared build steps

Install the platform's GTK ≥ 4, libadwaita ≥ 1.8, introspection data, ffmpeg,
PortAudio, gettext, a compiler and pkg-config first. On Linux also install binutils,
patchelf, file, ALSA data/plugins, Xvfb, dbus, Flatpak, flatpak-builder and elfutils. Then:

```sh
uv sync --locked --group packaging
uv run --locked --group packaging scripts/package.py freeze
uv run --locked --group packaging scripts/package.py archive
```

`freeze` compiles Blueprint and catalogs before running `packaging/kikoe.spec`.
PyInstaller includes Python, application data, GTK/libadwaita, typelibs, icon themes,
ffmpeg with its linked libraries, PortAudio, SDKs and keyring backends. The runtime
hook locates data, schemas and PortAudio inside the bundle. ffmpeg lookup understands
PyInstaller's `_internal` and macOS Frameworks layouts, without needing a host ffmpeg.
Set `KIKOE_NATIVE_PREFIX` when the native stack lives outside the system prefix.

Each archive/installer/AppImage/DMG/Flatpak has a `.sha256` file. `build-info.json` records the
source commit, Python, platform, architecture and installed package versions. The
bundle includes the application license and available native redistribution notices.
Do not publish a binary release without supplying the corresponding GPL source and
checking the licenses of the native toolchain's included libraries.

CI runs the frozen app with `--self-test`, then tests AppImage, installed Flatpak,
mounted DMG and the installed Windows application. The test opens GTK, imports all
providers and keyring, checks compiled UI/catalog/version data and creates synthetic
FLAC using bundled ffmpeg. It does not contact a provider or access the microphone.
Linux CI starts a PulseAudio null sink with its monitor as the default source, since
headless runners have no audio hardware for PortAudio initialization.
Windows also tests silent install and uninstall, including an installation path with
spaces. Review these checks separately from the hardware acceptance checklist below.

For an ordinary Python wheel, run `uv run scripts/build_ui.py`,
`uv run scripts/i18n.py compile`, then `uv build`. Generated `.ui`/`.mo` are included
via hatch `artifacts`.

Because UI files, icons, CSS and translations live inside `src/kikoe/`, every packager
only has to ship the Python package, GTK/libadwaita, ffmpeg, and PortAudio.
Microphone capture uses sounddevice/CFFI; NumPy and libsndfile are not required.

## Linux — AppImage and Flatpak

```sh
uv run --locked --group packaging scripts/package.py appimage
flatpak remote-add --user --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
uv run --locked --group packaging scripts/package.py flatpak
flatpak install --user dist/Kikoe-*.flatpak
```

AppImage uses appimagetool 1.9.1 and type-2 runtime 20251108, checked against committed SHA-256 hashes for both
architectures. `AppRun` resolves the frozen directory relative to itself and forwards
arguments. In CI, `APPIMAGE_EXTRACT_AND_RUN=1` exercises the image without requiring
FUSE. On hosts without FUSE, use `./Kikoe-*.AppImage --appimage-extract-and-run`.

The Flatpak manifest is generated under `build/` from the app constants and the frozen
directory. It reuses the same Python/native payload as AppImage, so it does not need
network access during the module build or rely on the runtime's Python version.
`org.gnome.Platform`/`org.gnome.Sdk` version 50 supplies the sandbox base. The generated
manifest installs the launcher, desktop file, metainfo and icon under `/app`.

Permissions are Wayland, fallback X11, IPC, DRI, PulseAudio (microphone), network and
`org.freedesktop.secrets` for keyring. There is no general filesystem or session-bus
grant. Import/export uses the file chooser portal. The host must provide a working
Secret Service keyring and a portal/audio service; sandbox microphone verification
remains a hardware acceptance step. ffmpeg and PortAudio are bundled directly.
The bundle includes ALSA data/plugins. Flatpak explicitly routes the ALSA backend
through its PulseAudio socket; an AppImage retains the host ALSA configuration.

## macOS — PyInstaller `.app`

- GTK, libadwaita, PyGObject from Homebrew; freeze with PyInstaller (it has hooks for
  `gi`). Gaphor's build is a good reference.
- Bundle Homebrew ffmpeg and its dynamic dependencies in the application Frameworks.
- Microphone access requires `NSMicrophoneUsageDescription` in `Info.plist`, for example
  "Kikoe records your microphone to turn speech into text notes." Verify the system
  permission prompt, denial and subsequent permission changes with the packaged app.
- Bundle the PortAudio library used by sounddevice; verify PyInstaller includes the
  platform library from the wheel or Homebrew installation.
- `uv run --locked --group packaging scripts/package.py dmg` creates the DMG.
- PyInstaller ad-hoc signs review builds. For a distribution build, set
  `KIKOE_CODESIGN_IDENTITY` to an installed Developer ID Application identity before
  `freeze`; the spec includes audio-input and library-validation entitlements.
  Submit the resulting DMG with `xcrun notarytool submit --wait` using your configured
  Apple credentials, then `xcrun stapler staple` it. Signing/notarization credentials
  are not stored in this repository and release notarization is not automated.

## Windows — MSYS2 + PyInstaller

- Build in MSYS2 UCRT64 for x64 and CLANGARM64 on a Windows ARM64 runner. GTK,
  libadwaita, Python 3.14, PyGObject, Cairo, CFFI, cryptography, PyInstaller, ffmpeg
  and PortAudio come from the matching native pacman prefix.
- `packaging/windows/build.sh` runs `scripts/package_windows.py` with a uv-managed
  official CPython driver. uv currently rejects MinGW Python's platform tag, so the
  driver installs SHA-256-verified **universal** wheels from `uv.lock` into an isolated
  build directory. GI/Cairo/CFFI/cryptography remain native pacman packages.
  jiter/pydantic-core are built from hashed, locked sources with Rust/maturin for the
  native interpreter. Blueprint and PyInstaller run with that same target SDK
  interpreter; the uv driver's interpreter is not shipped. MSVC extension wheels
  cannot enter the MinGW payload. `build-info.json` records pacman/Python versions.
- Bundle ffmpeg, PortAudio and their DLLs inside `_internal`. Verify Windows microphone
  privacy settings on real hardware.
- Python gettext translates `.ui` templates into the user cache before GTK loads them,
  including on systems without `locale.bindtextdomain`. Bundle the compiled `zh_CN`
  catalog and verify both template and Python labels in the packaged app.
- Installer: `packaging/windows/kikoe.iss` (Inno Setup 6), per-user installation,
  matching architecture restrictions, Start menu entry and optional desktop shortcut.
  The workflow passes the version/app ID from their source definitions. Review
  installers are unsigned; public releases need an Authenticode certificate and
  signing of the application and installer.

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
