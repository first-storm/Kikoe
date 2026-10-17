#!/usr/bin/env bash
# Compile native Liquid Glass resources and a separate macOS 15 fallback.
# Usage: bash packaging/macos/build-icons.sh OUTPUT_DIR
set -euo pipefail

source_dir=$(cd "$(dirname "$0")" && pwd)
output=${1:?Usage: build-icons.sh OUTPUT_DIR}
mkdir -p "$output"
output=$(cd "$output" && pwd)

xcode_version=$(xcodebuild -version | sed -n '1s/^Xcode //p')
if [[ ${xcode_version%%.*} -lt 26 ]]; then
    echo "Liquid Glass icons require full Xcode 26 or later; select it with DEVELOPER_DIR." >&2
    exit 1
fi
xcrun --find actool >/dev/null

# Remove only this script's outputs, so stale resources cannot hide a failed build.
rm -f "$output/Assets.car" "$output/AppIcon.icns" "$output/icon-info.plist"
rm -rf "$output/AppIcon.iconset"

# Target the layered resource at Tahoe. Older systems use the independent .icns,
# avoiding actool's version-dependent generation of legacy icon renditions.
xcrun actool "$source_dir/AppIcon.icon" \
    --compile "$output" --platform macosx --target-device mac \
    --minimum-deployment-target 26.0 --app-icon AppIcon \
    --output-format human-readable-text --notices --warnings --errors \
    --output-partial-info-plist "$output/icon-info.plist"

test -s "$output/Assets.car"
test -s "$output/icon-info.plist"
mkdir -p "$output/AppIcon.iconset"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$source_dir/AppIcon-fallback.png" \
        --out "$output/AppIcon.iconset/icon_${size}x${size}.png" >/dev/null
    retina=$((size * 2))
    sips -z "$retina" "$retina" "$source_dir/AppIcon-fallback.png" \
        --out "$output/AppIcon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil --convert icns "$output/AppIcon.iconset" --output "$output/AppIcon.icns"
test -s "$output/AppIcon.icns"
