# Packaging plan

Nothing here is automated yet. This records the intended approach so code stays
compatible with it.

## Shared build steps

1. `uv run scripts/build_ui.py` — `.blp` → `.ui` (inside the package).
2. `uv run scripts/i18n.py compile` — `.po` → `.mo` (inside the package).
3. Build a wheel: `uv build`. Generated `.ui`/`.mo` are included via hatch `artifacts`.

Because UI files, icons, CSS and translations live inside `src/kikoe/`, every packager
only has to ship the Python package, GTK/libadwaita, and ffmpeg.

## Linux — Flatpak (primary)

- Runtime: `org.gnome.Platform` (the first version whose Python is ≥ 3.14).
- Python deps: generate a module with `flatpak-pip-generator` from `uv export`.
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
- Microphone access requires `NSMicrophoneUsageDescription` in `Info.plist`.
- Sign and notarize for distribution.

## Windows — MSYS2 + PyInstaller

- Build in the MSYS2 UCRT64 environment (GTK, libadwaita, PyGObject from pacman) and
  freeze with PyInstaller; or use gvsbuild for an MSVC toolchain.
- Bundle `ffmpeg.exe` next to `kikoe.exe`.
- Translations of `.ui` strings need a fallback because Python lacks
  `locale.bindtextdomain` on Windows (see `kikoe/i18n.py`).
- Installer: Inno Setup or MSIX.

## Cross-platform pitfalls to avoid in code

- Paths: only `pathlib` and `kikoe.paths`; never assume `/` or `~/.config`.
- Subprocess: list arguments, no shell, find binaries via helpers (`find_ffmpeg`).
- No Linux-only libraries (D-Bus, GSettings, libsecret direct use, PulseAudio APIs)
  outside an explicitly platform-guarded module.
- File names: avoid characters invalid on Windows (`<>:"/\|?*`) when deriving names
  from note titles.
