"""Print the lines of a requirements file whose distributions are not installed yet.

MSYS2 provides some compiled dependencies as system packages, possibly in other
versions than uv.lock. Those are kept; only the missing ones are installed with pip.
"""

import re
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


def _installed(name: str) -> bool:
    try:
        distribution(name)
    except PackageNotFoundError:
        return False
    return True


def main() -> None:
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        match = _NAME.match(line)
        if match is None or _installed(match[1]):
            continue
        sys.stdout.write(line + "\n")


main()
