"""Check macOS icon build orchestration without Apple tools or network access."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_BUILD_SCRIPT = Path(__file__).resolve().parents[1] / "packaging/macos/build-icons.sh"
_FAKE_TOOL = """#!/usr/bin/env bash
set -euo pipefail
case "${0##*/}" in
    xcodebuild)
        echo "Xcode ${TEST_XCODE_VERSION:-26.3}"
        if [[ ${TEST_XCODE_LONG_OUTPUT:-0} = 1 ]]; then
            printf '%65536s\\n' 'Build version 17C100'
        fi
        ;;
    xcrun)
        if [[ $1 = --find ]]; then
            echo "$0"
            exit 0
        fi
        printf '%s\\n' "$@" > "$TEST_ICON_LOG"
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --compile) output=$2; shift ;;
                --output-partial-info-plist) info=$2; shift ;;
            esac
            shift
        done
        if [[ ${TEST_COMPILE_FAILURE:-0} = 1 ]]; then
            exit 7
        fi
        if [[ ${TEST_MISSING_ASSETS:-0} != 1 ]]; then
            echo 'native layered icon' > "$output/Assets.car"
        fi
        echo '<plist><dict/></plist>' > "$info"
        ;;
    sips)
        # Require a real source PNG and the expected output naming/dimensions.
        test "$1" = -z
        test "$2" = "$3"
        test -s "$4"
        test "$5" = --out
        printf '%s %s\\n' "$2" "${6##*/}" >> "$TEST_SIZE_LOG"
        cp "$4" "$6"
        ;;
    iconutil)
        test "$1" = --convert
        test "$2" = icns
        test "$(find "$3" -name '*.png' | wc -l | tr -d ' ')" = 10
        test "$4" = --output
        echo 'legacy icon' > "$5"
        ;;
esac
"""


@pytest.fixture
def icon_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stub only Apple tools so the real build script controls the output flow."""
    if shutil.which("bash") is None:
        pytest.skip("The macOS packaging script requires bash")
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in ("xcodebuild", "xcrun", "sips", "iconutil"):
        tool = tools / name
        tool.write_text(_FAKE_TOOL)
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("TEST_ICON_LOG", str(tmp_path / "compile.log"))
    monkeypatch.setenv("TEST_SIZE_LOG", str(tmp_path / "sizes.log"))
    return tmp_path / "icon output"


def test_macos_icons_build_native_and_all_legacy_sizes(icon_tools: Path) -> None:
    subprocess.run(["bash", str(_BUILD_SCRIPT), str(icon_tools)], check=True)

    assert (icon_tools / "Assets.car").is_file()
    assert (icon_tools / "AppIcon.icns").is_file()
    arguments = (icon_tools.parent / "compile.log").read_text().splitlines()
    assert arguments[arguments.index("--app-icon") + 1] == "AppIcon"
    assert arguments[arguments.index("--minimum-deployment-target") + 1] == "26.0"
    assert arguments[arguments.index("--platform") + 1] == "macosx"
    sizes = (icon_tools.parent / "sizes.log").read_text().splitlines()
    expected = {
        f"{size * scale} icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
        for size in (16, 32, 128, 256, 512)
        for scale in (1, 2)
    }
    assert set(sizes) == expected


def test_macos_icons_consume_complete_xcode_version_output(
    icon_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_XCODE_LONG_OUTPUT", "1")

    subprocess.run(["bash", str(_BUILD_SCRIPT), str(icon_tools)], check=True)

    assert (icon_tools / "Assets.car").is_file()


@pytest.mark.parametrize("failure", ["TEST_COMPILE_FAILURE", "TEST_MISSING_ASSETS"])
def test_macos_icons_reject_failed_compile_and_stale_outputs(
    icon_tools: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    icon_tools.mkdir()
    for name in ("Assets.car", "AppIcon.icns", "icon-info.plist"):
        (icon_tools / name).write_text("stale")
    monkeypatch.setenv(failure, "1")

    result = subprocess.run(["bash", str(_BUILD_SCRIPT), str(icon_tools)], check=False)

    assert result.returncode != 0
    assert not (icon_tools / "Assets.car").exists()
    assert not (icon_tools / "AppIcon.icns").exists()


def test_macos_icons_explain_old_xcode(icon_tools: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_XCODE_VERSION", "16.4")

    result = subprocess.run(
        ["bash", str(_BUILD_SCRIPT), str(icon_tools)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "require full Xcode 26" in result.stderr
    assert not (icon_tools.parent / "compile.log").exists()
