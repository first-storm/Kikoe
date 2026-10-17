# Packaging and releases

Kikoe distributes native packages for Linux, macOS and Windows. GitHub Actions
builds them from the shared wheel workflow, with platform-specific libraries and
installers. This guide covers the build pipeline, installation requirements,
release publication and acceptance checks.

| Platform | Package | Architectures | Workflow |
| --- | --- | --- | --- |
| Linux | Flatpak bundle (`.flatpak`) | x86_64, aarch64 | `package-linux.yml` |
| Linux | AppImage | x86_64, aarch64 | `package-linux.yml` |
| macOS 15+ | Disk image containing `Kikoe.app` | arm64, x86_64 | `package-macos.yml` |
| Windows 10/11 | Inno Setup installer and portable ZIP | x64, arm64 | `package-windows.yml` |

Workflows live in [`.github/workflows`](../.github/workflows). Each architecture
builds natively, including Arm runners for Linux and Windows and Intel/Arm runners
for macOS. Packaging pull-request workflows are restricted to their relevant
paths to control build cost. In private repositories, macOS and Windows consume
more of the Actions minutes allowance than Linux.

## Shared build pipeline

1. `wheel.yml` runs on Ubuntu 26.04, compiles Blueprints and translations, builds
   with `uv build`, and exports locked runtime requirements to `requirements.txt`.
   Hatch includes generated `.ui` and `.mo` files as wheel artifacts.
2. `packaging/ffmpeg/build.sh` builds ffmpeg, libopus and LAME from pinned,
   hash-verified sources for AppImage, macOS and Windows. ffmpeg comes from the
   `FFmpeg/FFmpeg` GitHub tag archive. Builds are cached by architecture.
3. `packaging/pyinstaller/kikoe.spec` freezes the wheel into a one-folder
   application. ffmpeg sits beside the executable, where `kikoe.media.ffmpeg`
   resolves it. `rthook_portaudio.py` selects bundled PortAudio.
4. Package jobs run startup smoke checks. AppImage, macOS and Windows must remain
   running for 20 seconds. The headless Flatpak job checks imports and ffmpeg
   instead of launching a graphical window.

The common `packaging/ffmpeg/configure.sh` retains audio decoders, demuxers and
filters, but limits encoders to libopus, LAME (MP3), FLAC and PCM; muxers to Ogg,
FLAC, MP3, WAV, PCM and null; and protocols to file and pipe. Flatpak uses the same
configuration, with LAME as its own module because the runtime does not guarantee
it. LAME is LGPL, so ffmpeg stays LGPL. When updating ffmpeg, Opus or LAME, update
version constants and hashes together, plus the Flatpak manifest's ffmpeg tag and
commit and its lame archive. On Windows, `automake-wrapper` supplies a `config.sub`
recent enough for LAME on arm64.

Startup smoke checks establish that a package loads. Microphone, playback and
real-provider acceptance are separate checks described below.

## Linux: Flatpak

The manifest is
[`packaging/flatpak/io.github.first_storm.Kikoe.yml`](../packaging/flatpak/io.github.first_storm.Kikoe.yml).
It uses `org.gnome.Platform//51`, with Python 3.14 and Freedesktop SDK 26.08, and
builds the `stable` application branch.

Generate `packaging/flatpak/python3-dependencies.json` from `uv.lock` with:

```sh
uv run scripts/flatpak_deps.py
```

CI checks that it matches the lockfile. The generated module uses locked wheels,
including architecture-specific compiled wheels, and builds PyGObject and pycairo
from locked source releases with Meson. PortAudio 19.7 and ffmpeg are separate
modules. PortAudio uses ALSA, routed through the runtime to PulseAudio/PipeWire.

The manifest installs the CI-built wheel from `dist/`. A Flathub source build
would require a blueprint-compiler module and building the wheel within the
manifest. The current distribution uses bundles and a self-hosted repository.

### Permissions and installation

The sandbox grants Wayland and fallback X11, IPC, DRI, PulseAudio for microphone
and playback, network access, and `org.freedesktop.secrets` access for keyring.
Import and export use the file chooser portal.

Install a downloaded bundle with:

```sh
flatpak install --user Kikoe-<version>-linux-<arch>.flatpak
```

The GNOME runtime is supplied by Flathub. For repository-based installation and
updates, use the signed remote:

```sh
flatpak remote-add --user --if-not-exists kikoe https://first-storm.github.io/Kikoe/kikoe.flatpakrepo
flatpak install --user kikoe io.github.first_storm.Kikoe
flatpak update
```

### Signed Flatpak repository

Publishing a release triggers `publish-flatpak-repo.yml`. It downloads that
release's bundles and runs:

```sh
packaging/flatpak/publish-repo.sh BUNDLE_DIR SITE_DIR
```

The script creates and signs an OSTree repository, generates static deltas,
`kikoe.flatpakrepo`, a `.flatpakref` and an index page, then the workflow deploys
it to GitHub Pages. Each deployment rebuilds from the selected release; it does
not retain older releases in the repository.

Manual dispatch accepts a release tag. Its assets must use the `stable` branch,
as v0.2.0 and later do. The `master` bundles from v0.1.x are incompatible with
this publication path.

Before first publication:

1. Set repository **Pages → Source** to **GitHub Actions**.
2. Generate an armored signing key without a passphrase and store it as the
   repository secret `FLATPAK_GPG_PRIVATE_KEY`:

   ```sh
   gpg --batch --passphrase '' --quick-gen-key 'Kikoe Flatpak <email>' ed25519 sign never
   gpg --armor --export-secret-keys '<email>'
   ```

3. Back up the private key. Clients pin the public key embedded in
   `kikoe.flatpakrepo`; replacing it requires users to re-add the remote.

The publication script has been checked locally with a test application, temporary
key and installation over local HTTP. GitHub Pages deployment and the repository
secret require an end-to-end publication check before being treated as verified.

## Linux: AppImage

[`packaging/linux/build-appimage.sh`](../packaging/linux/build-appimage.sh) runs
in an Ubuntu 26.04 container, the oldest Ubuntu used here with libadwaita 1.8.
The AppImage bundles GTK, libadwaita, Python and PortAudio, but uses host glibc.
It therefore requires glibc at least as new as Ubuntu 26.04. Use Flatpak on older
distributions.

ALSA (`libasound`) also comes from the host so its audio plugins and configuration
remain available. Make the AppImage executable before running it:

```sh
chmod +x Kikoe-<version>-linux-<arch>.AppImage
./Kikoe-<version>-linux-<arch>.AppImage
```

FUSE is required for normal execution. Set `APPIMAGE_EXTRACT_AND_RUN=1` to run
through extraction when FUSE is unavailable.

## macOS: application and disk image

[`packaging/macos/build.sh`](../packaging/macos/build.sh) uses Homebrew GTK,
libadwaita and Adwaita icons, with Python 3.14 supplied by uv. PyInstaller collects
the native libraries into `Kikoe.app`. Their build baseline requires macOS 15 or
newer, reflected in `LSMinimumSystemVersion`.

### App icon

macOS 26 and later use the layered Liquid Glass icon in
`packaging/macos/AppIcon.icon`. macOS 15 uses the independent rounded-square
`AppIcon.icns` fallback. Linux, Windows and the in-app GTK icon keep the shared
SVG. This changes the macOS package icon only, not the GTK interface material.

The macOS build requires full Xcode 26 or later, not just Command Line Tools.
CI explicitly selects Xcode 26.3 on both macOS 15 runners, preserving the macOS 15
deployment baseline. For a local build, select the installed Xcode with
`DEVELOPER_DIR` or `xcode-select` before invoking `packaging/macos/build.sh`.
The icon step can also run independently:

```sh
DEVELOPER_DIR=/Applications/Xcode_26.3.app/Contents/Developer \
  bash packaging/macos/build-icons.sh build/macos-icons
```

Edit `AppIcon.icon` in Apple's Icon Composer. Its 1024×1024 SVG layers separate
the document, transcript, blue badge and microphone. Keep those layers free of
baked lighting, shadows and an outer rounded mask; Icon Composer supplies the
glass effects and the system applies the enclosure. The document contains
default, dark and tinted/mono color settings. See Apple's
[Icon Composer guide](https://developer.apple.com/documentation/xcode/creating-your-app-icon-using-icon-composer).

`AppIcon-fallback.svg` is the editable static macOS 15 counterpart, with its own
rounded enclosure and static shadows. After changing the artwork, update it to
match the default composition and regenerate the committed 1024×1024 PNG with
Homebrew's existing librsvg tool:

```sh
rsvg-convert --width 1024 --height 1024 packaging/macos/AppIcon-fallback.svg \
  --output packaging/macos/AppIcon-fallback.png
```

`build-icons.sh` compiles the `.icon` with `actool` into `Assets.car` and an icon
Info.plist fragment, targeting macOS 26 for the layered resource only. It creates
all standard 1×/2× `.icns` sizes from the fallback PNG using `sips` and `iconutil`.
Missing tools, failed compilation and missing outputs fail the build. Generated
resources stay under `build/`; only artwork sources and the fallback PNG are
committed.

The build passes `KIKOE_MACOS_ICON_DIR` and `KIKOE_ICON` to PyInstaller. Its spec
collects `Assets.car` into `Contents/Resources` before signing, merges the
compiler's icon metadata, and uses `CFBundleIconName=AppIcon` plus
`CFBundleIconFile=AppIcon.icns`. Before creating the disk image, the build checks
those resources and references and verifies the bundle's code signature.

On macOS 26+, visually check the packaged app in Dock and Finder using default,
dark, clear and tinted system icon appearances, including small sizes. On macOS
15, check the rounded static fallback and startup. Linux can validate the source
artwork and packaging orchestration, but cannot run `actool` or verify native
Liquid Glass rendering; those checks require macOS. The current change has no
recorded macOS compilation or visual acceptance yet.

### Bundled engines and permissions

Unlike the other packages, the macOS app ships the transcribe.cpp local engine:
`scripts/bundle_engine.py` downloads its pinned binding and native library for the
build machine's architecture (Metal on arm64, CPU on x86_64), verifies them, and
`KIKOE_BUNDLED_ENGINES` makes the spec copy them to `local-engines/`. They are then
signed with the rest of the app, so no downloaded native code needs to be loaded.
Models are still downloaded at runtime.

`Info.plist` includes `NSMicrophoneUsageDescription`, explaining that Kikoe
records microphone audio to turn speech into text notes.

The app uses ad-hoc signing. Developer ID signing and notarization require
certificates configured as repository secrets. Without those, first launch may
require right-click → Open or removing quarantine:

```sh
xattr -dr com.apple.quarantine /Applications/Kikoe.app
```

## Windows: installer and portable ZIP

[`packaging/windows/build.sh`](../packaging/windows/build.sh) runs in MSYS2
UCRT64 for x64 and CLANGARM64 for Arm64. Compiled dependencies such as cffi,
cryptography, pydantic and pycairo come from pacman because PyPI wheels do not
cover MSYS2 Python. `packaging/windows/missing_requirements.py` selects remaining
requirements for pip; PyGObject and, when necessary, jiter build from source.
Pacman versions can differ from `uv.lock`; `pip check` reports conflicts in the
build log.

The frozen folder becomes a portable ZIP.
[`packaging/windows/kikoe.iss`](../packaging/windows/kikoe.iss) produces a per-user
Inno Setup installer, with per-machine installation available on request. The x64
installer can run under emulation on Windows on Arm; the Arm64 installer is native.
Executables lack Authenticode signatures, so SmartScreen may warn at first launch.

Python gettext translates templates into the user cache before GTK loads them,
including systems without native gettext bindings. All 14 bundled translations
cover Python and template labels. Chinese catalogs use `zh_Hans` and `zh_Hant`,
with regional system locales accepted. PyInstaller also collects GTK's own
translations under GTK's regional catalog names.

## Preparing and publishing a release

Kikoe is GPL-3.0-or-later; AppStream metadata is CC0-1.0. Packages include LICENSE
and full-color and symbolic icons. The UI requires libadwaita 1.8, including
`Adw.ShortcutsDialog`.

Before building a wheel, compile templates and catalogs and validate metadata:

```sh
uv run scripts/build_ui.py
uv run scripts/i18n.py compile
uv build
appstreamcli validate --no-net data/io.github.first_storm.Kikoe.metainfo.xml
```

Release screenshots in `data/screenshots/` contain synthetic content. AppStream
URLs reference files on `main`; check that they resolve after pushing release
content. Local `--no-net` validation does not verify remote image availability.

Pushing a `v*` tag invokes `release.yml`, builds all platform packages and attaches
them to a **draft release**. Review artifacts and acceptance results before
publishing. Publication then triggers the Flatpak Pages workflow.

### Validation coverage

The recorded checks establish the following scope; they do not imply that every
packaged platform or hosted service has passed hardware acceptance.

| Area | Recorded coverage | Additional acceptance needed |
| --- | --- | --- |
| Linux microphone capture | Default device, native-rate mono int16, pause/resume, FLAC and Opus conversion | Packaged checks on each target platform |
| Linux playback | Generated full recording and bounded sample, default output and cleanup | macOS/Windows hardware and packaged playback |
| GTK interface | 1180×780 and 360 px, light/dark; recording, samples, matching and dialogs | Packaged platform behavior |
| Search, preferences and localization | English/Simplified Chinese layouts, defaults, language validation, shortcuts and retry propagation | Native packaged startup on macOS/Windows |
| Provider requests | Fake responses and streams; 16/24 kHz resampling; Mistral loopback WebSocket with its SDK | Real-account file and live sessions |
| Flatpak publication | Local repository signing and installation | GitHub Pages and configured signing secret |

Signing/notarization on macOS, Authenticode on Windows and Flathub source-build
submission are distribution requirements beyond the current package configuration.

### Microphone and recovery acceptance

Test each packaged platform with a configured file-transcription model:

1. Record a new note and confirm that timer and level meter reflect actual input.
2. Pause, resume and stop; verify saved duration excludes the pause.
3. Export Original Audio and play the FLAC; both captured intervals must be present.
4. Deny microphone permission or remove the default device; verify an actionable error.
5. Fail or cancel transcription; confirm audio survives and retry updates the same
   recording. Close while capturing and check retained audio after restart.
6. Interrupt capture by terminating the process, relaunch and choose Recover.
   Recovery must not upload until an explicit retry.

Linux checks cover these capture fundamentals. macOS and Windows microphone
acceptance requires real hardware; package startup alone is insufficient.

### Hosted-provider acceptance

Test each configured provider's file model and the OpenAI, Deepgram, Gemini and
Mistral live endpoints with spoken text, multiple turns, pause/resume, network
interruption and catch-up, Stop during catch-up, and original FLAC export.
Include Deepgram speaker turns and a Gemini recording longer than ten minutes.
A live-only model must require explicit file-model selection for retry. Closing
and recovery must preserve audio and confirmed text without automatic upload.

Mistral's fake-response and loopback tests leave these real-service details to
confirm: word-timestamp response shape, diarization granularity requirements,
500 MB/60-minute bounds where documentation differs, stream event order, and the
realtime quiet-period flush behavior.

### Playback acceptance

On each packaged platform, play a retained recording and a timed speaker sample,
switch targets, stop, and start microphone capture. Confirm that only one output
stream exists and no decoder remains after closing the dialog or window. Missing
devices, missing original audio and permission failures must produce readable
messages.

Playback uses bundled ffmpeg and sounddevice/PortAudio. The chosen output device
must accept mono int16 at its advertised native rate. Automated tests use fake
devices and temporary WAV files to check seeking, cancellation, decoder failures
and cleanup without real hardware.

## Local engines

Except on macOS, local transcription engines are not part of any package. Users
download them in Preferences → Local Models from the engine's own GitHub release,
pinned by SHA-256 in `providers/local/engines/<engine>/catalog.json`; models come
from Hugging Face at pinned revisions. Files go to `data_dir()/local`, which Flatpak
already allows. The worker runs on the packaged Python, so frozen builds need no
extra files: `launch.py` starts it for `--engine-worker`.

The Flatpak's `--device=dri` covers Vulkan. CUDA additionally needs the NVIDIA
device nodes, which the manifest does not request; users who want it run:

```sh
flatpak override --user --device=all io.github.first_storm.Kikoe
```

Updating an engine means changing its catalog entries; the receipt of an older
install still works until the user downloads the new version.

## Portability and local diagnostics

Use `pathlib` and `kikoe.paths` for locations, argument lists for subprocesses,
and helpers such as `find_ffmpeg` for binaries. Platform-specific APIs belong
behind explicit guards and fallbacks. File names derived from note titles must
avoid Windows-invalid characters (`<>:"/\|?*`).

Logs stay local, rotating at 2 MiB with three backups. Data and log folders open
through `Gtk.FileLauncher` so platform launchers or portals handle access. If file
logging fails, terminal diagnostics remain available. Library relocation and
automatic error-report uploads are unavailable.
