#!/usr/bin/env bash
# Build a small, self-contained ffmpeg for bundling with Kikoe (see configure.sh for
# what is included). libopus is linked statically, so the result only depends on
# system libraries. Runs on Linux, macOS and in an MSYS2 shell.
#
# Usage: packaging/ffmpeg/build.sh <install prefix>
# The executable ends up in <prefix>/bin/ffmpeg (ffmpeg.exe on Windows).
set -euo pipefail

FFMPEG_VERSION=8.1.2
FFMPEG_SHA256=464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c
OPUS_VERSION=1.5.2
OPUS_SHA256=65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1

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
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" "$FFMPEG_SHA256"

deps="$work/deps"
(
    cd "$work/opus-$OPUS_VERSION"
    ./configure --prefix="$deps" --enable-static --disable-shared \
        --disable-doc --disable-extra-programs
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

cd "$work/ffmpeg-$FFMPEG_VERSION"
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
