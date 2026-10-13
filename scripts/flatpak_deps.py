"""Generate the Flatpak module that installs Kikoe's locked Python dependencies.

Usage:
    uv run scripts/flatpak_deps.py          # rewrite packaging/flatpak/python3-dependencies.json
    uv run scripts/flatpak_deps.py --check  # fail if the module no longer matches uv.lock

Flatpak builds have no network access, so every wheel is listed as a source with its
URL and hash, taken from ``uv export``. Pure-Python wheels are shared; compiled ones
are listed once per architecture. PyGObject and pycairo have no Linux wheels; they are
built from their locked source releases with Meson against the runtime's libraries.
"""

import argparse
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packaging.markers import Marker

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "packaging" / "flatpak" / "python3-dependencies.json"

ARCHES = ("x86_64", "aarch64")
PYTHON_TAG = "cp314"
# Built with Meson, in this order (PyGObject needs pycairo's headers).
MESON_PACKAGES = ("pycairo", "pygobject")
MARKER_ENVIRONMENT = {
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "platform_system": "Linux",
    "python_full_version": "3.14.0",
    "python_version": "3.14",
    "sys_platform": "linux",
}

_WHEEL_NAME = re.compile(
    r"^(?P<name>[^-]+)-(?P<version>[^-]+)(-\d[^-]*)?-(?P<python>[^-]+)-(?P<abi>[^-]+)-(?P<platform>[^-]+)\.whl$"
)
_MANYLINUX = re.compile(r"^manylinux(?:_(\d+)_(\d+)|2014)_(\w+)$")


@dataclass(frozen=True, slots=True)
class Wheel:
    url: str
    sha256: str

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[1]


def locked_packages() -> list[dict[str, Any]]:
    """Return the runtime packages from uv's PEP 751 export (TOML values are untyped)."""
    exported = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "pylock.toml",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    packages: list[dict[str, Any]] = tomllib.loads(exported)["packages"]
    return packages


def wheels_of(package: dict[str, Any]) -> list[Wheel]:
    entries: list[dict[str, Any]] = package.get("wheels", [])
    return [Wheel(str(entry["url"]), str(entry["hashes"]["sha256"])) for entry in entries]


def is_pure(wheel: Wheel) -> bool:
    return wheel.filename.endswith("-none-any.whl")


def glibc_requirement(wheel: Wheel, arch: str) -> tuple[int, int] | None:
    """Return the glibc version a CPython 3.14 wheel for ``arch`` needs, or None."""
    match = _WHEEL_NAME.match(wheel.filename)
    if match is None:
        return None
    pythons = match["python"].split(".")
    abi = match["abi"]
    usable = (abi == PYTHON_TAG and PYTHON_TAG in pythons) or (
        abi == "abi3" and any(_abi3_compatible(tag) for tag in pythons)
    )
    if not usable:
        return None
    versions: list[tuple[int, int]] = []
    for platform in match["platform"].split("."):
        tag = _MANYLINUX.match(platform)
        if tag is None or tag[3] != arch:
            continue
        versions.append((int(tag[1]), int(tag[2])) if tag[1] else (2, 17))
    return min(versions, default=None)


def _abi3_compatible(tag: str) -> bool:
    return tag.startswith("cp3") and tag[3:].isdigit() and int(tag[3:]) <= int(PYTHON_TAG[3:])


def sources_for(package: dict[str, Any]) -> list[dict[str, object]]:
    name = str(package["name"])
    wheels = wheels_of(package)
    if pure := next((wheel for wheel in wheels if is_pure(wheel)), None):
        return [{"type": "file", "url": pure.url, "sha256": pure.sha256}]

    sources: list[dict[str, object]] = []
    for arch in ARCHES:
        candidates = [
            (requirement, wheel)
            for wheel in wheels
            if (requirement := glibc_requirement(wheel, arch)) is not None
        ]
        if not candidates:
            sys.exit(f"{name}: no manylinux {arch} wheel for {PYTHON_TAG}; add a source build.")
        _, wheel = min(candidates, key=lambda candidate: candidate[0])
        sources.append(
            {"type": "file", "url": wheel.url, "sha256": wheel.sha256, "only-arches": [arch]}
        )
    return sources


def meson_module(package: dict[str, Any]) -> dict[str, object]:
    sdist: dict[str, Any] = package["sdist"]
    return {
        "name": str(package["name"]),
        "buildsystem": "meson",
        "config-opts": ["-Dtests=false"],
        "sources": [
            {"type": "archive", "url": str(sdist["url"]), "sha256": str(sdist["hashes"]["sha256"])}
        ],
    }


def module() -> dict[str, object]:
    sources: list[dict[str, object]] = []
    meson: dict[str, dict[str, object]] = {}
    for package in locked_packages():
        name = str(package["name"])
        marker = package.get("marker")
        if marker is not None and not Marker(str(marker)).evaluate(MARKER_ENVIRONMENT):
            continue
        if name in MESON_PACKAGES:
            meson[name] = meson_module(package)
        else:
            sources.extend(sources_for(package))
    return {
        "name": "python3-dependencies",
        "buildsystem": "simple",
        "build-commands": [
            "pip3 install --no-index --no-deps --no-build-isolation"
            ' --prefix="${FLATPAK_DEST}" *.whl'
        ],
        "sources": sources,
        "modules": [meson[name] for name in MESON_PACKAGES],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is outdated")
    args = parser.parse_args()

    text = json.dumps(module(), indent=2) + "\n"
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.is_file() else ""
        if current != text:
            print(f"{OUTPUT.relative_to(ROOT)} is outdated. Run: uv run scripts/flatpak_deps.py")
            return 1
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
