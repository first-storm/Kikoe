"""Platform detection used to pick engine builds."""

import pytest

from kikoe.core.local_models import Architecture, OperatingSystem, Platform
from kikoe.providers.local import hardware


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("linux", "x86_64", Platform(OperatingSystem.LINUX, Architecture.X86_64)),
        ("linux", "aarch64", Platform(OperatingSystem.LINUX, Architecture.ARM64)),
        ("darwin", "arm64", Platform(OperatingSystem.MACOS, Architecture.ARM64)),
        ("win32", "AMD64", Platform(OperatingSystem.WINDOWS, Architecture.X86_64)),
        ("linux", "riscv64", None),
        ("freebsd14", "amd64", None),
    ],
)
def test_current_platform_normalizes_names(
    monkeypatch: pytest.MonkeyPatch, system: str, machine: str, expected: Platform | None
) -> None:
    monkeypatch.setattr(hardware.sys, "platform", system)
    monkeypatch.setattr(hardware.platform, "machine", lambda: machine)
    assert hardware.current_platform() == expected


def test_flatpak_is_recognised_by_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert not hardware.in_flatpak()
    monkeypatch.setenv("FLATPAK_ID", "io.github.first_storm.Kikoe")
    assert hardware.in_flatpak()
