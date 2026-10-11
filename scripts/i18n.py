"""Maintain translations. Needs GNU gettext (xgettext, msgmerge, msgfmt) on PATH.

Usage:
    uv run scripts/i18n.py update   # regenerate po/kikoe.pot and merge into each .po
    uv run scripts/i18n.py compile  # build .mo files into src/kikoe/locale/

Strings are extracted from Python sources and from the compiled .ui files, so run
scripts/build_ui.py first. Languages are listed in po/LINGUAS.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "kikoe"
PO = ROOT / "po"
DOMAIN = "kikoe"
POT = PO / f"{DOMAIN}.pot"


def tool(name: str) -> str:
    exe = shutil.which(name)
    if exe is None:
        sys.exit(f"{name} not found. Install GNU gettext.")
    return exe


def languages() -> list[str]:
    lines = (PO / "LINGUAS").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def update() -> None:
    py_files = sorted(p.relative_to(ROOT).as_posix() for p in SRC.rglob("*.py"))
    ui_files = sorted(p.relative_to(ROOT).as_posix() for p in SRC.rglob("*.ui"))
    if not ui_files:
        sys.exit("No .ui files found. Run scripts/build_ui.py first.")
    common = [
        "--from-code=UTF-8",
        "--add-comments=Translators:",
        f"--package-name={DOMAIN}",
        "--msgid-bugs-address=https://github.com/first-storm/Kikoe/issues",
        "--sort-by-file",
    ]
    xgettext = tool("xgettext")
    subprocess.run(
        [
            xgettext,
            *common,
            "--language=Python",
            "--keyword=_",
            "--keyword=ngettext:1,2",
            f"--output={POT}",
            *py_files,
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [xgettext, *common, "--language=Glade", "--join-existing", f"--output={POT}", *ui_files],
        cwd=ROOT,
        check=True,
    )
    for lang in languages():
        po = PO / f"{lang}.po"
        if po.exists():
            subprocess.run([tool("msgmerge"), "--update", "--backup=none", po, POT], check=True)
    print(f"updated {POT.relative_to(ROOT)}")


def compile_all() -> None:
    for lang in languages():
        out = SRC / "locale" / lang / "LC_MESSAGES" / f"{DOMAIN}.mo"
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [tool("msgfmt"), "--check", f"--output-file={out}", PO / f"{lang}.po"], check=True
        )
        print(f"compiled {out.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=["update", "compile"])
    args = parser.parse_args()
    if args.command == "update":
        update()
    else:
        compile_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
