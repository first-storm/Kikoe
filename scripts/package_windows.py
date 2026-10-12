"""Use uv for locked pure dependencies and MSYS2 tools for native Windows binaries."""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
PAYLOAD = BUILD / "windows-python"
NATIVE_PACKAGES = {"pygobject", "pycairo", "cffi", "cryptography", "jiter", "pydantic-core"}


class Source(TypedDict):
    """Hashed download entry from uv.lock."""

    url: str
    hash: str


def run(*command: str, env: dict[str, str] | None = None, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def locked_packages() -> dict[str, dict[str, object]]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = cast(list[dict[str, object]], lock["package"])
    return {str(package["name"]): package for package in packages}


def requirements(packages: dict[str, dict[str, object]]) -> Path:
    """Choose universal wheels so MSVC extensions never enter the MinGW payload."""
    result = subprocess.run(
        ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "--no-header"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    lines: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement, separator, marker = line.partition(" ; ")
        name, version = requirement.split("==", 1)
        if name in NATIVE_PACKAGES:
            continue
        package = packages[name]
        if str(package["version"]) != version:
            raise RuntimeError(f"Lock version mismatch for {name}")
        wheels = cast(list[Source], package.get("wheels", []))
        wheel = next((source for source in wheels if source["url"].endswith("-none-any.whl")), None)
        if wheel is None:
            raise RuntimeError(f"No universal wheel for {name}; add an explicit native build")
        suffix = f" ; {marker}" if separator else ""
        lines.append(f"{name} @ {wheel['url']}{suffix} \\\n    --hash={wheel['hash']}")
    output = BUILD / "windows-pure-requirements.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def extract_wheel(wheel: Path) -> None:
    """Install a locally built MinGW wheel without asking uv to interpret its ABI tag."""
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(PAYLOAD)


def native_extension(name: str, package: dict[str, object], interpreter: Path) -> None:
    """Build the locked Rust extensions using MSYS2's interpreter and Rust target."""
    source = cast(Source, package["sdist"])
    directory = BUILD / "native-sources" / name
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
    download = directory / "source.tar.gz"
    with urllib.request.urlopen(source["url"]) as response, download.open("wb") as destination:
        shutil.copyfileobj(response, destination)
    with download.open("rb") as content:
        digest = hashlib.file_digest(content, "sha256").hexdigest()
    if "sha256:" + digest != source["hash"]:
        raise RuntimeError(f"Source checksum mismatch for {name}")
    with tarfile.open(download) as archive:
        archive.extractall(directory, filter="data")
    project = next(path for path in directory.iterdir() if path.is_dir())
    wheels = directory / "wheels"
    run(
        "maturin",
        "build",
        "--release",
        "--interpreter",
        str(interpreter),
        "--out",
        str(wheels),
        cwd=project,
    )
    for wheel in wheels.glob("*.whl"):
        extract_wheel(wheel)


def main() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Run this driver in an MSYS2 shell on Windows")
    BUILD.mkdir(exist_ok=True)
    if PAYLOAD.exists():
        shutil.rmtree(PAYLOAD)
    PAYLOAD.mkdir(exist_ok=True)
    packages = locked_packages()
    prefix = Path(os.environ["KIKOE_NATIVE_PREFIX"])
    interpreter = prefix / "bin/python.exe"
    pure_requirements = requirements(packages)
    run(
        "uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "--target",
        str(PAYLOAD),
        "--no-deps",
        "--require-hashes",
        "-r",
        str(pure_requirements),
    )
    # The compiler is pure Python; its GTK introspection runs under the target
    # SDK interpreter selected by KIKOE_NATIVE_PYTHON below.
    run(
        "uv",
        "pip",
        "install",
        "--python",
        sys.executable,
        "--target",
        str(PAYLOAD),
        "--no-deps",
        "blueprint-compiler @ git+https://gitlab.gnome.org/GNOME/blueprint-compiler.git@v0.22.2",
    )
    for name in ("jiter", "pydantic-core"):
        native_extension(name, packages[name], interpreter)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(PAYLOAD), str(ROOT / "src")))
    env["KIKOE_NATIVE_PYTHON"] = str(interpreter)
    run(sys.executable, "scripts/build_ui.py", env=env)
    run(sys.executable, "scripts/i18n.py", "compile", env=env)
    run(sys.executable, "-m", "hatchling", "build", "-t", "wheel", env=env)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(project["project"]["version"])
    extract_wheel(ROOT / "dist" / f"kikoe-{version}-py3-none-any.whl")
    run(sys.executable, "scripts/package.py", "freeze", env=env)
    run(sys.executable, "scripts/package.py", "archive", env=env)


if __name__ == "__main__":
    main()
