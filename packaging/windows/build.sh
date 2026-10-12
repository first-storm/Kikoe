#!/usr/bin/env bash
set -euo pipefail

# Use the MinGW interpreter that matches the native GTK and PyInstaller packages.
# Upstream CPython wheels are not ABI-compatible with this interpreter.
export UV_PYTHON_DOWNLOADS=never
uv venv --python "$(command -v python)" --system-site-packages
export PYTHONPATH="$PWD/src"
uv run --no-sync scripts/package.py windows-requirements
export _PYTHON_HOST_PLATFORM
_PYTHON_HOST_PLATFORM=$(uv run --no-sync python -c 'import sysconfig; print(sysconfig.get_platform())')
export PYO3_PYTHON
PYO3_PYTHON=$(uv run --no-sync python -c 'import sys; print(sys.executable)')

# Keep GI/Cairo/CFFI/cryptography from pacman. Build the two Rust extensions for
# MinGW rather than installing MSVC wheels. websockets has a pure-Python fallback.
uv pip install --python .venv --no-deps --no-build-isolation \
  --no-binary-package jiter --no-binary-package pydantic-core \
  --no-binary-package websockets --no-binary-package charset-normalizer \
  -r build/windows-requirements.txt
uv pip install --python .venv --no-deps \
  'blueprint-compiler @ git+https://gitlab.gnome.org/GNOME/blueprint-compiler.git@v0.22.2'
uv run --no-sync scripts/build_ui.py
uv run --no-sync scripts/i18n.py compile
uv run --no-sync python -m hatchling build -t wheel
uv pip install --python .venv --no-deps dist/kikoe-*.whl
uv run --no-sync scripts/package.py freeze
uv run --no-sync scripts/package.py archive
