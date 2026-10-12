#!/usr/bin/env bash
# Build the Windows one-folder app and a portable zip, in an MSYS2 shell.
#
# Runs in UCRT64 (x64) or CLANGARM64 (arm64). The MSYS2 packages listed in the
# workflow must be installed. The wheel and requirements.txt from the wheel build
# must be in dist/; ffmpeg is built into build/ffmpeg unless it is already there.
#
# Usage: packaging/windows/build.sh
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
build="$root/build"
case "$MSYSTEM" in
    CLANGARM64) arch=arm64 ;;
    *) arch=x64 ;;
esac

if [[ ! -x "$build/ffmpeg/bin/ffmpeg.exe" ]]; then
    packaging/ffmpeg/build.sh "$build/ffmpeg"
fi

wheel=$(ls dist/kikoe-*.whl)
version=$(basename "$wheel" | cut -d- -f2)

# PyPI has no wheels for MSYS2's Python. Compiled dependencies come from MSYS2 where
# it packages them, through the system site-packages; the rest is installed from the
# lock file, and PyGObject and jiter are built from source.
python -m venv --clear --system-site-packages "$build/venv"
venv_python=$(ls "$build"/venv/{bin,Scripts}/python.exe 2>/dev/null | head -n 1)
"$venv_python" packaging/windows/missing_requirements.py dist/requirements.txt \
    >"$build/requirements.txt"
"$venv_python" -m pip install --no-build-isolation --requirement "$build/requirements.txt"
"$venv_python" -m pip install --no-build-isolation --no-deps "$wheel"
"$venv_python" -m pip check || echo "warning: MSYS2 package versions differ from uv.lock"

rsvg-convert --width 256 --height 256 \
    src/kikoe/ui/icons/hicolor/scalable/apps/io.github.first_storm.Kikoe.svg \
    --output "$build/icon.png"

KIKOE_FFMPEG_BIN="$build/ffmpeg/bin/ffmpeg.exe" KIKOE_ICON="$build/icon.png" \
    "$venv_python" -m PyInstaller --noconfirm --clean \
    --distpath "$build/dist" --workpath "$build/pyinstaller" \
    packaging/pyinstaller/kikoe.spec

mkdir -p "$root/out"
(cd "$build/dist" && "$venv_python" -m zipfile -c "$root/out/Kikoe-$version-windows-$arch.zip" Kikoe)
