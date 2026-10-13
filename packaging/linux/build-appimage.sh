#!/usr/bin/env bash
# Build the Kikoe AppImage for the host architecture (x86_64 or aarch64).
#
# Run on the oldest distribution that has the GTK and libadwaita versions Kikoe needs
# (the AppImage does not bundle glibc). Needs uv, the GTK/PortAudio packages listed in
# the workflow, and the wheel and requirements.txt from the wheel build in dist/.
# ffmpeg is built into build/ffmpeg unless it is already there. Downloads
# appimagetool unless APPIMAGETOOL points to it.
#
# Usage: packaging/linux/build-appimage.sh
set -euo pipefail

app_id=io.github.first_storm.Kikoe
root=$(cd "$(dirname "$0")/../.." && pwd)
cd "$root"
arch=$(uname -m)
build="$root/build"

if [[ ! -x "$build/ffmpeg/bin/ffmpeg" ]]; then
    packaging/ffmpeg/build.sh "$build/ffmpeg"
fi

wheel=$(ls dist/kikoe-*.whl)
version=$(basename "$wheel" | cut -d- -f2)

uv venv --clear --python 3.14 "$build/venv"
export VIRTUAL_ENV="$build/venv"
uv pip install --requirement dist/requirements.txt "$wheel" pyinstaller==6.22.3

KIKOE_FFMPEG_BIN="$build/ffmpeg/bin/ffmpeg" \
    "$build/venv/bin/pyinstaller" --noconfirm --clean \
    --distpath "$build/dist" --workpath "$build/pyinstaller" \
    packaging/pyinstaller/kikoe.spec

appdir="$build/Kikoe.AppDir"
rm -rf "$appdir"
mkdir -p "$appdir/usr/lib" "$appdir/usr/share/applications" "$appdir/usr/share/metainfo" \
    "$appdir/usr/share/icons/hicolor/scalable/apps" "$appdir/usr/share/icons/hicolor/symbolic/apps"
cp -a "$build/dist/Kikoe" "$appdir/usr/lib/kikoe"

cat >"$appdir/AppRun" <<'EOF'
#!/bin/sh
here=$(dirname "$(readlink -f "$0")")
exec "$here/usr/lib/kikoe/kikoe" "$@"
EOF
chmod 755 "$appdir/AppRun"

icons=src/kikoe/ui/icons/hicolor/scalable/apps
install -m644 "data/$app_id.desktop" "$appdir/usr/share/applications/"
install -m644 "data/$app_id.metainfo.xml" "$appdir/usr/share/metainfo/"
install -m644 "$icons/$app_id.svg" "$appdir/usr/share/icons/hicolor/scalable/apps/"
install -m644 "$icons/$app_id-symbolic.svg" "$appdir/usr/share/icons/hicolor/symbolic/apps/"
ln -s "usr/share/applications/$app_id.desktop" "$appdir/$app_id.desktop"
ln -s "usr/share/icons/hicolor/scalable/apps/$app_id.svg" "$appdir/$app_id.svg"
ln -s "$app_id.svg" "$appdir/.DirIcon"

tool=${APPIMAGETOOL:-}
if [[ -z "$tool" ]]; then
    tool="$build/appimagetool"
    curl --fail --location --retry 4 --silent --show-error --output "$tool" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$arch.AppImage"
    chmod 755 "$tool"
fi

mkdir -p out
# Containers have no FUSE; let the tool unpack itself instead of mounting.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH="$arch" VERSION="$version" \
    "$tool" --no-appstream "$appdir" "out/Kikoe-$version-linux-$arch.AppImage"
