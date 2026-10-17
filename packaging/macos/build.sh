#!/usr/bin/env bash
# Build Kikoe.app and a disk image on macOS (arm64 or x86_64, matching the host).
#
# Needs macOS 26 with full Xcode 26+ (or prebuilt icons), Homebrew and uv.
# The wheel and requirements.txt must be
# in dist/; ffmpeg is built into build/ffmpeg unless it is already there.
#
# Usage: packaging/macos/build.sh
set -euo pipefail

root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
arch=$(uname -m)
build="$root/build"

# CI compiles architecture-independent icons on macOS 26, then packages on macOS 15.
if [[ ${KIKOE_PREBUILT_MACOS_ICONS:-0} != 1 ]]; then
    bash packaging/macos/build-icons.sh "$build/macos-icons"
fi
for resource in Assets.car AppIcon.icns icon-info.plist; do
    test -s "$build/macos-icons/$resource"
done

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

# transcribe.cpp is small on macOS, so it ships inside the app instead of being downloaded.
rm -rf "$build/engines"
uv run --active --no-sync scripts/bundle_engine.py "$build/engines" transcribe-cpp

KIKOE_FFMPEG_BIN="$build/ffmpeg/bin/ffmpeg" KIKOE_ICON="$build/macos-icons/AppIcon.icns" \
    KIKOE_MACOS_ICON_DIR="$build/macos-icons" \
    KIKOE_BUNDLED_ENGINES="$build/engines" \
    "$build/venv/bin/pyinstaller" --noconfirm --clean \
    --distpath "$build/dist" --workpath "$build/pyinstaller" \
    packaging/pyinstaller/kikoe.spec

# Check the signed bundle before copying it to the disk image.
app="$build/dist/Kikoe.app"
test -s "$app/Contents/Resources/Assets.car"
test -s "$app/Contents/Resources/AppIcon.icns"
test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconName' "$app/Contents/Info.plist")" = AppIcon
test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIconFile' "$app/Contents/Info.plist")" = AppIcon.icns
codesign --verify --deep --strict --verbose=2 "$app"

staging="$build/dmg"
rm -rf "$staging"
mkdir -p "$staging" "$root/out"
cp -R "$build/dist/Kikoe.app" "$staging/"
ln -s /Applications "$staging/Applications"
hdiutil create -volname Kikoe -srcfolder "$staging" -fs HFS+ -format UDZO -ov \
    "$root/out/Kikoe-$version-macos-$arch.dmg"
