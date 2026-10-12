#!/usr/bin/env bash
set -euo pipefail

# uv cannot manage a MinGW interpreter. Use official CPython for the uv driver,
# then native MSYS2 console tools to compile Blueprint, Rust extensions and freeze.
export KIKOE_NATIVE_PREFIX
KIKOE_NATIVE_PREFIX=$(cygpath -w "$MINGW_PREFIX")
export PYTHONPATH="$PWD/src"
export PYTHONUTF8=1
uv run --no-project --python "$KIKOE_UV_PYTHON" --with 'hatchling>=1.27' scripts/package_windows.py
