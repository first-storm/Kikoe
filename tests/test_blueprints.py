import shutil
import subprocess
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "kikoe"
BLUEPRINTS = sorted(SRC.rglob("*.blp"))

pytestmark = pytest.mark.gtk


@pytest.mark.parametrize("blp", BLUEPRINTS, ids=lambda p: p.name)
def test_blueprint_compiles(blp: Path, tmp_path: Path) -> None:
    exe = shutil.which("blueprint-compiler")
    if exe is None:
        pytest.skip("blueprint-compiler not installed")

    result = subprocess.run(
        [exe, "compile", "--output", str(tmp_path / "out.ui"), str(blp)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_every_blueprint_has_a_python_class() -> None:
    for blp in BLUEPRINTS:
        assert blp.with_suffix(".py").is_file(), f"{blp.name} has no matching .py module"
