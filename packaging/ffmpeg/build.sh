#!/usr/bin/env bash
# Build a small, self-contained ffmpeg for bundling with Kikoe (see configure.sh for
# what is included). libopus and LAME are linked statically, so the result only depends
# on system libraries. Runs on Linux, macOS and in an MSYS2 shell.
#
# Usage: packaging/ffmpeg/build.sh <install prefix>
# The executable ends up in <prefix>/bin/ffmpeg (ffmpeg.exe on Windows).
set -euo pipefail

# ffmpeg comes from the GitHub mirror's tag archive: ffmpeg.org often fails to resolve
# on CI runners. Keep the version in step with the Flatpak manifest's tag and commit.
FFMPEG_VERSION=8.1.2
FFMPEG_SHA256=9fd092511605bbebafe095ea6d38d9e40f34d12f7386e1258372df8be0576eb7
OPUS_VERSION=1.5.2
OPUS_SHA256=65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1
# Keep LAME in step with the Flatpak manifest's lame module.
LAME_VERSION=3.100
LAME_SHA256=ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e

here=$(cd "$(dirname "$0")" && pwd)
prefix=$(mkdir -p "$1" && cd "$1" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

case "$(uname -s)" in
    Darwin) jobs=$(sysctl -n hw.ncpu) ;;
    *) jobs=$(nproc) ;;
esac

fetch() {
    local url=$1 sha=$2 out
    out="$work/$(basename "$url")"
    curl --fail --location --retry 4 --silent --show-error --output "$out" "$url"
    # macOS's sha256sum lacks GNU options, and shasum is missing from slim Linux images.
    actual=$({ sha256sum "$out" 2>/dev/null || shasum -a 256 "$out"; } | cut -d' ' -f1)
    if [[ "$actual" != "$sha" ]]; then
        echo "Checksum mismatch for $url: expected $sha, got $actual" >&2
        exit 1
    fi
    tar -xf "$out" -C "$work"
}

fetch "https://downloads.xiph.org/releases/opus/opus-$OPUS_VERSION.tar.gz" "$OPUS_SHA256"
fetch "https://downloads.sourceforge.net/project/lame/lame/$LAME_VERSION/lame-$LAME_VERSION.tar.gz" \
    "$LAME_SHA256"
fetch "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n$FFMPEG_VERSION.tar.gz" "$FFMPEG_SHA256"

opus_configure=()
case "${MSYSTEM:-}" in
    # Opus has no ARM CPU detection for Windows; NEON is always present on arm64.
    CLANGARM64) opus_configure+=(--disable-rtcd) ;;
esac

deps="$work/deps"
(
    cd "$work/opus-$OPUS_VERSION"
    ./configure --prefix="$deps" --enable-static --disable-shared \
        --disable-doc --disable-extra-programs \
        ${opus_configure[@]+"${opus_configure[@]}"}
    make -j"$jobs"
    make install
)
(
    cd "$work/lame-$LAME_VERSION"
    # LAME's 2017 config.sub predates targets such as aarch64-w64-mingw32.
    if automake_dir=$(automake --print-libdir 2>/dev/null); then
        cp "$automake_dir/config.guess" "$automake_dir/config.sub" .
    fi
    ./configure --prefix="$deps" --enable-static --disable-shared \
        --disable-frontend --disable-decoder
    make -j"$jobs"
    make install
)

extra_configure=()
extra_ldflags=""
case "${MSYSTEM:-}" in
    CLANG*) extra_configure+=(--cc=clang --cxx=clang++) ;;
esac
if [[ -n "${MSYSTEM:-}" ]]; then
    # Link the MinGW runtime statically so ffmpeg.exe needs no extra DLLs.
    extra_ldflags="-static"
fi
case "$(uname -m)" in
    x86_64 | AMD64 | amd64)
        command -v nasm >/dev/null || extra_configure+=(--disable-x86asm)
        ;;
esac

cd "$work/FFmpeg-n$FFMPEG_VERSION"
PKG_CONFIG_PATH="$deps/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}" \
    bash "$here/configure.sh" \
    --prefix="$prefix" \
    --pkg-config-flags=--static \
    --extra-cflags="-I$deps/include" \
    --extra-ldflags="-L$deps/lib $extra_ldflags" \
    ${extra_configure[@]+"${extra_configure[@]}"}
make -j"$jobs"
make install

"$prefix/bin/ffmpeg" -hide_banner -version | head -n 1
