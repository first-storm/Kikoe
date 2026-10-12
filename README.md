# Kikoe

Kikoe turns speech into text notes. It records or imports audio and transcribes it with
cloud speech-to-text services (Deepgram, OpenAI, OpenRouter, Gemini AI Studio, or an
OpenAI-compatible API), with
optional timestamps and speaker labels, and exports to text, Markdown, SRT, or WebVTT.

Built with Python 3.14, GTK 4 and libadwaita. Linux is the primary platform; macOS and
Windows are planned (see [docs/packaging.md](docs/packaging.md)).

> Status: provider management, audio import, microphone recording with pause/resume,
> batch and realtime transcription, audio/text recovery, and transcript/audio export
> are implemented. All four hosted providers use their official Python SDKs.
> Real provider accounts and macOS/Windows realtime capture are not yet verified.

## Transcription

Choose an API type and add a model in Preferences. Capabilities can be declared for
custom models; known API restrictions override unsupported options. Deepgram Nova-3
supports file and live transcription with timestamps and speaker detection. OpenRouter
supports file STT. OpenAI and Gemini also offer live transcription, with timestamps and
speaker options disabled where the live endpoint cannot supply them.

`gpt-live-transcribe` and `gemini-3.5-transcribe-live` are microphone-only models.
Select a file model (for example `whisper-1`, `gpt-4o-transcribe-diarize`, or
`gemini-3.5-transcribe`) to import audio or retry an incomplete recording. Live success
is saved directly without a second file upload.

Recording always saves audio locally. After a network interruption Kikoe reconnects
and replays unconfirmed audio from disk. If transcription remains incomplete at stop,
the confirmed text and FLAC are kept. Select a file model and use Retry Transcription;
failed or cancelled retries preserve the existing text. Recovery never uploads audio
automatically. Speaker IDs from independent recordings/connections are kept separate;
matching people between them remains part of M6.

## Development setup

Requirements on every platform:

- [uv](https://docs.astral.sh/uv/) (it installs Python 3.14 if needed)
- GTK 4 and libadwaita with GObject introspection data
- Headers to build PyGObject: `girepository-2.0` (GLib ≥ 2.80) and `cairo`, plus `pkg-config` and a C compiler
- `ffmpeg` on `PATH` (or set `KIKOE_FFMPEG`)
- PortAudio for microphone capture (`sounddevice`; no NumPy required)
- GNU gettext, only when working on translations

Platform packages:

| Platform | Command |
| --- | --- |
| openSUSE | `sudo zypper in gtk4 libadwaita-1 typelib-1_0-Adw-1 cairo-devel glib2-devel ffmpeg gettext-tools libportaudio2` |
| Fedora | `sudo dnf install gtk4 libadwaita cairo-devel gobject-introspection-devel ffmpeg-free gettext portaudio` |
| Debian/Ubuntu | `sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 libgirepository-2.0-dev libcairo2-dev pkg-config ffmpeg gettext libportaudio2` |
| macOS (Homebrew) | `brew install gtk4 libadwaita gobject-introspection pkg-config ffmpeg gettext portaudio` |
| Windows | Use the MSYS2 UCRT64 shell: `pacman -S mingw-w64-ucrt-x86_64-{gtk4,libadwaita,gobject-introspection,cairo,pkgconf,gcc,ffmpeg,gettext,portaudio}` |

Then:

```sh
uv sync                        # create .venv with runtime + dev dependencies
uv run scripts/build_ui.py     # compile Blueprint (.blp) files to .ui
uv run kikoe                   # start the app
```

Re-run `scripts/build_ui.py` whenever a `.blp` file changes.
Set `KIKOE_LOG_LEVEL=DEBUG` for verbose logs.

## Checks

```sh
uv run ruff check              # lint
uv run ruff format             # format
uv run scripts/build_ui.py --check   # Blueprint formatting + lint
uv run pyright                 # type check (strict)
uv run pytest                  # tests
```

CI runs all of these on every push.

## Documentation

- [AGENTS.md](AGENTS.md): coding rules for humans and AI assistants. Read before contributing.
- [docs/architecture.md](docs/architecture.md): layers, data flow, async model.
- [docs/design.md](docs/design.md): UI specification for every screen.
- [docs/packaging.md](docs/packaging.md): distribution plan per platform.
- [po/README.md](po/README.md): translations.
