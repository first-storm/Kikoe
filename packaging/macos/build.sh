#!/usr/bin/env bash
# Build Kikoe.app and a disk image on macOS (arm64 or x86_64, matching the host).
#
# Needs Homebrew and uv. The wheel and requirements.txt from the wheel build must be
# in dist/; ffmpeg is built into build/ffmpeg unless it is already there.
#
# Usage: packaging/macos/build.sh
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
arch=$(uname -m)
build="$root/build"

brew install --quiet gtk4 libadwaita adwaita-icon-theme gobject-introspection \
    pkgconf cairo libffi librsvg nasm
# libffi is keg-only, but PyGObject's build looks it up with pkg-config.
export PKG_CONFIG_PATH="$(brew --prefix libffi)/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
# Homebrew's libraries require the macOS version they were built for.
export MACOSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET:-$(sw_vers -productVersion | cut -d. -f1).0}

if [[ ! -x "$build/ffmpeg/bin/ffmpeg" ]]; then
    packaging/ffmpeg/build.sh "$build/ffmpeg"
fi

wheel=$(ls dist/kikoe-*.whl)
version=$(basename "$wheel" | cut -d- -f2)

uv venv --clear --python 3.14 "$build/venv"
export VIRTUAL_ENV="$build/venv"
uv pip install --requirement dist/requirements.txt "$wheel" pyinstaller==6.22.3 pillow==12.3.0

rsvg-convert --width 1024 --height 1024 \
    src/kikoe/ui/icons/hicolor/scalable/apps/io.github.first_storm.Kikoe.svg \
    --output "$build/icon.png"

KIKOE_FFMPEG_BIN="$build/ffmpeg/bin/ffmpeg" KIKOE_ICON="$build/icon.png" \
    "$build/venv/bin/pyinstaller" --noconfirm --clean \
    --distpath "$build/dist" --workpath "$build/pyinstaller" \
    packaging/pyinstaller/kikoe.spec

staging="$build/dmg"
rm -rf "$staging"
mkdir -p "$staging" "$root/out"
cp -R "$build/dist/Kikoe.app" "$staging/"
ln -s /Applications "$staging/Applications"
hdiutil create -volname Kikoe -srcfolder "$staging" -fs HFS+ -format UDZO -ov \
    "$root/out/Kikoe-$version-macos-$arch.dmg"
