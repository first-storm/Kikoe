"""Build native desktop bundles and installers with the current uv environment."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from importlib.metadata import distributions
from pathlib import Path

from kikoe.constants import APP_ID, VERSION

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICON = ROOT / "src/kikoe/ui/icons/hicolor/scalable/apps" / f"{APP_ID}.svg"
APPIMAGETOOL_VERSION = "1.9.1"
APPIMAGETOOL_HASHES = {
    "x86_64": "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
    "aarch64": "f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158",
}
APPIMAGE_RUNTIME_VERSION = "20251108"
APPIMAGE_RUNTIME_HASHES = {
    "x86_64": "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d",
    "aarch64": "00cbdfcf917cc6c0ff6d3347d59e0ca1f7f45a6df1a428a0d6d8a78664d87444",
}


def architecture() -> str:
    """Normalize native runner names and reject accidental 32-bit builds."""
    machine = platform.machine().lower()
    match machine:
        case "amd64" | "x86_64":
            return "x86_64"
        case "arm64" | "aarch64":
            return "aarch64"
        case _:
            raise RuntimeError(f"Unsupported architecture: {machine}")


def artifact_name(suffix: str) -> Path:
    return DIST / f"Kikoe-{VERSION}-{sys.platform}-{architecture()}{suffix}"


def run(*command: str, env: dict[str, str] | None = None, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def checksum(path: Path) -> None:
    """Write the digest alongside each distributable for download verification."""
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    path.with_name(path.name + ".sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def checksums() -> None:
    """Include installer hashes alongside the archive hashes."""
    for path in DIST.iterdir():
        if path.is_file() and path.suffix in (
            ".exe",
            ".zip",
            ".gz",
            ".dmg",
            ".flatpak",
            ".AppImage",
        ):
            checksum(path)


def download_verified(url: str, destination: Path, expected: str) -> None:
    """Fail closed if an upstream packaging executable changes."""
    run("curl", "--fail", "--location", "--retry", "3", url, "--output", str(destination))
    with destination.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if digest != expected:
        raise RuntimeError(f"Checksum mismatch for {destination.name}")


def native_prefix() -> Path:
    if configured := os.environ.get("KIKOE_NATIVE_PREFIX"):
        return Path(configured).resolve()
    if sys.platform == "darwin":
        result = subprocess.run(["brew", "--prefix"], check=True, capture_output=True, text=True)
        return Path(result.stdout.strip())
    if sys.platform == "win32":
        return Path(sys.base_prefix)
    return Path("/usr")


def native_icon(env: dict[str, str]) -> None:
    """Create executable icons from the same SVG that ships inside the app."""
    if sys.platform not in ("darwin", "win32"):
        return
    BUILD.mkdir(exist_ok=True)
    png = BUILD / "Kikoe.png"
    run("rsvg-convert", "--width", "1024", "--height", "1024", "--output", str(png), str(ICON))
    if sys.platform == "win32":
        # ICO supports a PNG payload on all supported Windows versions.
        small = BUILD / "Kikoe-256.png"
        run("rsvg-convert", "--width", "256", "--height", "256", "--output", str(small), str(ICON))
        payload = small.read_bytes()
        icon = BUILD / "Kikoe.ico"
        header = struct.pack("<HHH", 0, 1, 1)
        entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(payload), 22)
        icon.write_bytes(header + entry + payload)
        env["KIKOE_WINDOWS_ICON"] = str(icon)
        return
    iconset = BUILD / "Kikoe.iconset"
    iconset.mkdir(exist_ok=True)
    system_env = dict(os.environ)
    system_env.pop("DYLD_LIBRARY_PATH", None)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            suffix = "@2x" if scale == 2 else ""
            output = iconset / f"icon_{size}x{size}{suffix}.png"
            run(
                "rsvg-convert",
                "--width",
                str(size * scale),
                "--height",
                str(size * scale),
                "--output",
                str(output),
                str(ICON),
            )
    icon = BUILD / "Kikoe.icns"
    run("iconutil", "-c", "icns", str(iconset), "-o", str(icon), env=system_env)
    env["KIKOE_MACOS_ICON"] = str(icon)


def freeze() -> None:
    """Compile package resources and freeze the native interpreter and libraries."""
    architecture()
    run(sys.executable, str(ROOT / "scripts/build_ui.py"))
    run(sys.executable, str(ROOT / "scripts/i18n.py"), "compile")
    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("Install ffmpeg before packaging")
    env = dict(os.environ)
    env.update(
        KIKOE_NATIVE_PREFIX=str(native_prefix()),
        KIKOE_BUILD_FFMPEG=str(Path(executable).resolve()),
        KIKOE_APP_ID=APP_ID,
        KIKOE_VERSION=VERSION,
    )
    if sys.platform == "darwin":
        env["KIKOE_MACOS_ARCH"] = "arm64" if architecture() == "aarch64" else "x86_64"
    native_icon(env)
    packages = {dist.metadata["Name"]: dist.version for dist in distributions()}
    commit = os.environ.get("GITHUB_SHA")
    if commit is None:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        )
        commit = result.stdout.strip()
    information: dict[str, object] = {
        "app_id": APP_ID,
        "version": VERSION,
        "commit": commit,
        "platform": sys.platform,
        "architecture": architecture(),
        "python": platform.python_version(),
        "packages": packages,
    }
    if sys.platform == "win32":
        native_command = ["pacman", "-Q"]
    elif sys.platform == "darwin":
        native_command = ["brew", "list", "--versions"]
    else:
        native_command = ["dpkg-query", "-W"]
    if shutil.which(native_command[0]):
        native_result = subprocess.run(native_command, check=True, capture_output=True, text=True)
        information["native_packages"] = native_result.stdout.splitlines()
    BUILD.mkdir(exist_ok=True)
    (BUILD / "build-info.json").write_text(
        json.dumps(information, indent=2) + "\n", encoding="utf-8"
    )
    # Include provenance before signing: adding files to a signed .app breaks its seal.
    packager = [env.get("KIKOE_NATIVE_PYTHON", sys.executable), "-m", "PyInstaller"]
    run(
        *packager,
        "--noconfirm",
        "--clean",
        "packaging/kikoe.spec",
        env=env,
    )


def archive() -> None:
    """Create a portable archive of the frozen directory without losing symlinks."""
    if sys.platform == "darwin":
        output = artifact_name(".zip")
        run(
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(DIST / "Kikoe.app"),
            str(output),
        )
    else:
        extension, archive_format = (
            (".zip", "zip") if sys.platform == "win32" else (".tar.gz", "gztar")
        )
        output = artifact_name(extension)
        shutil.make_archive(
            str(output).removesuffix(extension),
            archive_format,
            DIST,
            "kikoe",
        )
    checksum(output)


def appimage() -> None:
    """Build a type-2 AppImage with a pinned, checksum-verified appimagetool."""
    if sys.platform != "linux":
        raise RuntimeError("AppImages must be built on Linux")
    appdir = BUILD / "Kikoe.AppDir"
    if appdir.exists():
        shutil.rmtree(appdir)
    shutil.copytree(DIST / "kikoe", appdir / "usr/lib/kikoe", symlinks=True)
    shutil.copy2(ROOT / "packaging/linux/AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)
    shutil.copy2(ROOT / "data" / f"{APP_ID}.desktop", appdir)
    shutil.copy2(ICON, appdir)
    tools = BUILD / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    tool = tools / f"appimagetool-{architecture()}.AppImage"
    url = f"https://github.com/AppImage/appimagetool/releases/download/{APPIMAGETOOL_VERSION}/{tool.name}"
    download_verified(url, tool, APPIMAGETOOL_HASHES[architecture()])
    tool.chmod(0o755)
    runtime = tools / f"runtime-{architecture()}"
    runtime_url = "https://github.com/AppImage/type2-runtime/releases/download/"
    runtime_url += f"{APPIMAGE_RUNTIME_VERSION}/{runtime.name}"
    download_verified(runtime_url, runtime, APPIMAGE_RUNTIME_HASHES[architecture()])
    output = artifact_name(".AppImage")
    env = dict(os.environ, ARCH=architecture(), APPIMAGE_EXTRACT_AND_RUN="1")
    run(
        str(tool),
        "--runtime-file",
        str(runtime),
        "--no-appstream",
        str(appdir),
        str(output),
        env=env,
    )
    checksum(output)


def flatpak_manifest() -> dict[str, object]:
    """Reuse the native bundle in an offline, portal-enabled GNOME sandbox."""
    return {
        "app-id": APP_ID,
        "runtime": "org.gnome.Platform",
        "runtime-version": "50",
        "sdk": "org.gnome.Sdk",
        "command": "kikoe",
        "finish-args": [
            "--socket=wayland",
            "--socket=fallback-x11",
            "--share=ipc",
            "--device=dri",
            "--socket=pulseaudio",
            "--share=network",
            "--talk-name=org.freedesktop.secrets",
        ],
        "modules": [
            {
                "name": "kikoe",
                "buildsystem": "simple",
                "build-commands": [
                    "mkdir -p /app/lib && cp -a frozen /app/lib/kikoe",
                    "install -Dm755 kikoe /app/bin/kikoe",
                    f"install -Dm644 {APP_ID}.desktop /app/share/applications/{APP_ID}.desktop",
                    f"install -Dm644 {APP_ID}.metainfo.xml "
                    f"/app/share/metainfo/{APP_ID}.metainfo.xml",
                    f"install -Dm644 {APP_ID}.svg "
                    f"/app/share/icons/hicolor/scalable/apps/{APP_ID}.svg",
                    "install -Dm644 LICENSE /app/share/licenses/kikoe/LICENSE",
                ],
                "sources": [
                    {"type": "dir", "path": str(DIST / "kikoe"), "dest": "frozen"},
                    {
                        "type": "file",
                        "path": str(ROOT / "packaging/linux/flatpak-launcher"),
                        "dest-filename": "kikoe",
                    },
                    {"type": "file", "path": str(ROOT / "data" / f"{APP_ID}.desktop")},
                    {"type": "file", "path": str(ROOT / "data" / f"{APP_ID}.metainfo.xml")},
                    {"type": "file", "path": str(ICON)},
                    {"type": "file", "path": str(ROOT / "LICENSE")},
                ],
            }
        ],
    }


def flatpak() -> None:
    """Export a Flatpak bundle that installs its runtime from Flathub."""
    if sys.platform != "linux":
        raise RuntimeError("Flatpaks must be built on Linux")
    manifest = BUILD / f"{APP_ID}.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(flatpak_manifest(), indent=2) + "\n", encoding="utf-8")
    repository = BUILD / "flatpak-repo"
    run(
        "flatpak-builder",
        "--user",
        "--force-clean",
        "--install-deps-from=flathub",
        f"--arch={architecture()}",
        f"--repo={repository}",
        str(BUILD / "flatpak"),
        str(manifest),
    )
    output = artifact_name(".flatpak")
    run(
        "flatpak",
        "build-bundle",
        f"--arch={architecture()}",
        "--runtime-repo=https://flathub.org/repo/flathub.flatpakrepo",
        str(repository),
        str(output),
        APP_ID,
    )
    checksum(output)


def dmg() -> None:
    """Create a drag-to-Applications macOS disk image."""
    if sys.platform != "darwin":
        raise RuntimeError("DMGs must be built on macOS")
    staging = BUILD / "dmg"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    run("ditto", str(DIST / "Kikoe.app"), str(staging / "Kikoe.app"))
    (staging / "Applications").symlink_to("/Applications")
    output = artifact_name(".dmg")
    run(
        "hdiutil",
        "create",
        "-ov",
        "-format",
        "UDZO",
        "-volname",
        "Kikoe",
        "-srcfolder",
        str(staging),
        str(output),
    )
    checksum(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = {
        "freeze": freeze,
        "archive": archive,
        "appimage": appimage,
        "flatpak": flatpak,
        "dmg": dmg,
        "checksums": checksums,
    }
    parser.add_argument("command", choices=commands)
    args = parser.parse_args()
    commands[args.command]()


if __name__ == "__main__":
    main()
