"""Layer, size and extension rules from AGENTS.md, checked on the source tree.

Some limits are ratchets: the sets below list the current exceptions. They may only
shrink; fix a violation and delete its entry rather than adding new ones.
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "kikoe"
TOP_LEVEL_HELPERS = {"kikoe.constants", "kikoe.paths", "kikoe.i18n"}
IO_ADAPTERS = ("providers", "media", "storage")
MAX_MODULE_LINES = 500

OVERSIZED_MODULES = {
    "storage/library.py",
    "ui/note_view.py",
    "ui/window.py",
}
"""Modules above MAX_MODULE_LINES today; each one is scheduled to be split."""

API_KIND_MEMBER_USERS = {
    "core/provider_registry.py",
    "providers/factory.py",
    "providers/mistral.py",
}
"""Modules that name a specific ``ApiKind.MEMBER``. Provider-specific rules belong in the
provider registry or the provider's own client, not in callers."""


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(SRC).as_posix()


def _package(path: Path) -> str:
    parts = path.relative_to(SRC).parts
    return parts[0] if len(parts) > 1 else ""


def _imports(path: Path) -> set[str]:
    """Absolute module names imported anywhere in ``path``, including lazy imports."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _kikoe_package(name: str) -> str | None:
    """The sub-package of a ``kikoe.*`` module name; None for other or top-level modules."""
    parts = name.split(".")
    if parts[0] != "kikoe" or len(parts) < 2 or name in TOP_LEVEL_HELPERS:
        return None
    return parts[1] if parts[1] in _PACKAGES else None


_PACKAGES = {p.name for p in SRC.iterdir() if p.is_dir() and (p / "__init__.py").exists()}


def _violations(package: str, forbidden: set[str]) -> list[str]:
    found: list[str] = []
    for path in _modules():
        if _package(path) != package:
            continue
        for name in _imports(path):
            target = _kikoe_package(name)
            if target in forbidden:
                found.append(f"{_relative(path)} imports {name}")
    return sorted(found)


def test_core_imports_only_the_standard_library_and_core() -> None:
    stdlib = set(sys.stdlib_module_names)
    found: list[str] = []
    for path in _modules():
        if _package(path) != "core":
            continue
        for name in _imports(path):
            root = name.split(".")[0]
            if root == "kikoe":
                target = _kikoe_package(name)
                if target not in (None, "core"):
                    found.append(f"{_relative(path)} imports {name}")
            elif root not in stdlib:
                found.append(f"{_relative(path)} imports {name}")
    assert sorted(found) == []


@pytest.mark.parametrize("package", IO_ADAPTERS)
def test_io_adapters_do_not_import_other_layers(package: str) -> None:
    forbidden = {"ui", "services", *(p for p in IO_ADAPTERS if p != package)}
    assert _violations(package, forbidden) == []


def test_services_do_not_import_the_ui() -> None:
    assert _violations("services", {"ui"}) == []


def test_module_size_stays_bounded() -> None:
    oversized = {
        _relative(p)
        for p in _modules()
        if len(p.read_text(encoding="utf-8").splitlines()) > MAX_MODULE_LINES
    }
    assert oversized - OVERSIZED_MODULES == set(), "new module above the size limit"
    assert OVERSIZED_MODULES - oversized == set(), "shrunk below the limit: remove its entry"


def _names_api_kind_member(path: Path) -> bool:
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ApiKind"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
    )


def test_api_kind_members_are_named_only_where_allowed() -> None:
    users = {_relative(p) for p in _modules() if _names_api_kind_member(p)}
    assert users - API_KIND_MEMBER_USERS == set(), "new ApiKind user: use the provider registry"
    assert API_KIND_MEMBER_USERS - users == set(), "no longer names a member: remove its entry"
