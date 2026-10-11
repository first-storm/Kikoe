"""Compile every Blueprint (.blp) under src/kikoe into a .ui file next to it.

Usage:
    uv run scripts/build_ui.py          # compile
    uv run scripts/build_ui.py --check  # check formatting + lint, compile nothing
    uv run scripts/build_ui.py --fix    # reformat .blp files in place
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "kikoe"


def blueprints() -> list[Path]:
    return sorted(SRC.rglob("*.blp"))


def compiler() -> str:
    exe = shutil.which("blueprint-compiler")
    if exe is None:
        sys.exit("blueprint-compiler not found. Run `uv sync` and use `uv run`.")
    return exe


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="check formatting and lint")
    group.add_argument("--fix", action="store_true", help="reformat Blueprint files")
    args = parser.parse_args()

    exe = compiler()
    files = [str(p) for p in blueprints()]
    if args.fix:
        return subprocess.run([exe, "format", "--fix", *files], check=False).returncode
    if args.check:
        fmt = subprocess.run([exe, "format", *files], check=False).returncode
        lint = subprocess.run([exe, "lint", "--no-suggestions", *files], check=False).returncode
        return fmt or lint

    for blp in blueprints():
        ui = blp.with_suffix(".ui")
        result = subprocess.run([exe, "compile", "--output", str(ui), str(blp)], check=False)
        if result.returncode != 0:
            return result.returncode
        print(f"compiled {ui.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
