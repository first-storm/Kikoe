"""Measure sidebar selection through a painted transcript frame using a database copy.

Usage: uv run scripts/benchmark_note_switch.py /path/to/library.sqlite3
Run on a desktop, or under xvfb-run. Only notes matching --prefix (default: test)
are opened. The supplied database is read-only and never migrated or edited.
"""

import argparse
import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("database", type=Path)
    parser.add_argument("--prefix", default="test")
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--limit-ms", type=float, default=50)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    database = Path(args.database).resolve()
    if args.repeats < 1:
        parser.error("--repeats must be at least 1")
    with TemporaryDirectory(prefix="kikoe-note-benchmark-") as directory:
        root = Path(directory)
        for category in ("DATA", "CONFIG", "CACHE", "STATE"):
            os.environ[f"XDG_{category}_HOME"] = str(root / category.lower())
        data = root / "data" / "Kikoe"
        data.mkdir(parents=True)
        with (
            closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(data / "library.sqlite3")) as destination,
        ):
            source.backup(destination)
        from kikoe.ui.benchmark import run

        measurements = run(
            args.prefix, args.repeats, lambda m: print(json.dumps(asdict(m)), flush=True)
        )
    report = {
        "limit_ms": args.limit_ms,
        "passed": all(m.milliseconds <= args.limit_ms for m in measurements),
        "measurements": [asdict(m) for m in measurements],
    }
    if args.output is not None:
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "measurements"}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
