"""Blueprint layouts have matching Python widget modules."""

from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "kikoe"
BLUEPRINTS = sorted(SRC.rglob("*.blp"))


def test_every_blueprint_has_a_python_class() -> None:
    for blp in BLUEPRINTS:
        assert blp.with_suffix(".py").is_file(), f"{blp.name} has no matching .py module"
