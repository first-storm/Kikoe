"""Engine catalogs: validation, merging and per-platform build lookup."""

import copy
from collections.abc import Callable
from typing import Any

import pytest

from kikoe.core.local_models import (
    Accelerator,
    Architecture,
    ArchiveKind,
    EngineBuild,
    LocalCatalogError,
    ModelRole,
    OperatingSystem,
    Platform,
    merge_catalogs,
    needs_device_access,
    parse_engine_catalog,
    recommend_build,
)
from kikoe.providers.local.engines import ENGINES, load_catalog

SHA = "a" * 64
LINUX = Platform(OperatingSystem.LINUX, Architecture.X86_64)


def document() -> dict[str, Any]:
    return {
        "version": 1,
        "engine": {
            "id": "engine",
            "name": "Engine",
            "version": "1.0",
            "homepage": "https://example.test",
            "components": [
                {
                    "url": "https://example.test/binding.whl",
                    "sha256": SHA,
                    "size": 10,
                    "archive": "zip",
                    "target": "python",
                }
            ],
            "builds": [
                {
                    "id": "linux-x86_64-gpu",
                    "os": "linux",
                    "arch": "x86_64",
                    "accelerators": ["vulkan", "cpu"],
                    "components": [
                        {
                            "url": "https://example.test/native.tar.gz",
                            "sha256": SHA,
                            "size": 20,
                            "archive": "tar.gz",
                            "target": "native",
                            "strip": 1,
                        }
                    ],
                },
                {
                    "id": "macos-arm64",
                    "os": "macos",
                    "arch": "arm64",
                    "accelerators": ["metal", "cpu"],
                    "components": [
                        {"url": "https://example.test/mac.tar.gz", "sha256": SHA, "size": 5}
                    ],
                },
            ],
        },
        "models": [
            {
                "id": "tiny",
                "name": "Tiny",
                "family": "Family",
                "file": {
                    "url": "https://example.test/models/tiny-Q8_0.gguf",
                    "sha256": SHA,
                    "size": 7,
                },
                "timestamps": True,
                "max_seconds": 600,
            }
        ],
    }


def test_a_valid_catalog_describes_engine_builds_and_models() -> None:
    engine, models = parse_engine_catalog(document())
    assert engine.engine_id == "engine"
    assert [b.build_id for b in engine.builds_for(LINUX)] == ["linux-x86_64-gpu"]
    build = engine.builds_for(LINUX)[0]
    assert build.accelerators == (Accelerator.VULKAN, Accelerator.CPU)
    downloads = engine.downloads(build)
    assert [d.archive for d in downloads] == [ArchiveKind.ZIP, ArchiveKind.TAR_GZ]
    assert downloads[1].strip == 1
    (model,) = models
    assert model.engine_id == "engine"
    assert model.role is ModelRole.ASR
    assert model.file.filename == "tiny-Q8_0.gguf"
    assert model.timestamps and not model.streaming
    assert model.max_seconds == 600
    assert model.languages == ()


def test_platforms_without_a_build_get_none() -> None:
    engine, _models = parse_engine_catalog(document())
    assert engine.builds_for(Platform(OperatingSystem.WINDOWS, Architecture.ARM64)) == ()


def _model(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["models"][0]


def _build(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["engine"]["builds"][0]


BREAKAGES: dict[str, Callable[[dict[str, Any]], None]] = {
    "version": lambda d: d.update(version=2),
    "http": lambda d: _model(d)["file"].update(url="http://example.test/tiny.gguf"),
    "sha": lambda d: _model(d)["file"].update(sha256="ABC"),
    "size": lambda d: _model(d)["file"].update(size=0),
    "no-cpu": lambda d: _build(d).update(accelerators=["vulkan"]),
    "accelerator": lambda d: _build(d).update(accelerators=["quantum", "cpu"]),
    "target": lambda d: _build(d)["components"][0].update(target="../escape"),
    "duplicate-model": lambda d: d["models"].append(copy.deepcopy(_model(d))),
    "duplicate-build": lambda d: d["engine"]["builds"].append(copy.deepcopy(_build(d))),
    "archive-model": lambda d: _model(d)["file"].update(archive="zip"),
    "no-name": lambda d: _model(d)["file"].update(url="https://example.test/models/"),
    "id": lambda d: _model(d).update(id="Bad ID"),
    "flag": lambda d: _model(d).update(timestamps="yes"),
}


@pytest.mark.parametrize("change", BREAKAGES)
def test_malformed_catalogs_are_rejected(change: str) -> None:
    doc = copy.deepcopy(document())
    BREAKAGES[change](doc)
    with pytest.raises(LocalCatalogError):
        parse_engine_catalog(doc)


def test_model_ids_must_be_unique_across_engines() -> None:
    first = parse_engine_catalog(document())
    other = copy.deepcopy(document())
    other["engine"]["id"] = "other"
    with pytest.raises(LocalCatalogError, match="duplicate model"):
        merge_catalogs([first, parse_engine_catalog(other)])


def test_bundled_catalogs_load_and_match_their_registry_ids() -> None:
    catalog = load_catalog()
    assert [e.engine_id for e in catalog.engines] == list(ENGINES)
    assert catalog.models
    for model in catalog.models:
        assert catalog.engine(model.engine_id) is not None


def test_transcribe_cpp_offers_a_build_for_every_desktop_platform() -> None:
    engine = load_catalog().engine("transcribe-cpp")
    assert engine is not None
    for system, arch in [
        (OperatingSystem.LINUX, Architecture.X86_64),
        (OperatingSystem.LINUX, Architecture.ARM64),
        (OperatingSystem.WINDOWS, Architecture.X86_64),
        (OperatingSystem.MACOS, Architecture.ARM64),
        (OperatingSystem.MACOS, Architecture.X86_64),
    ]:
        assert engine.builds_for(Platform(system, arch)), (system, arch)


def _builds(*lanes: tuple[str, tuple[Accelerator, ...]]) -> tuple[EngineBuild, ...]:
    return tuple(EngineBuild(name, LINUX, accelerators, ()) for name, accelerators in lanes)


@pytest.mark.parametrize(
    ("lanes", "expected"),
    [
        (
            (
                ("cuda", (Accelerator.CUDA, Accelerator.VULKAN, Accelerator.CPU)),
                ("vulkan", (Accelerator.VULKAN, Accelerator.CPU)),
            ),
            "vulkan",
        ),
        ((("metal", (Accelerator.METAL, Accelerator.CPU)),), "metal"),
        ((("cpu", (Accelerator.CPU,)),), "cpu"),
        ((("cuda", (Accelerator.CUDA, Accelerator.CPU)),), "cuda"),
    ],
)
def test_the_recommended_build_avoids_cuda_unless_it_is_the_only_one(
    lanes: tuple[tuple[str, tuple[Accelerator, ...]], ...], expected: str
) -> None:
    build = recommend_build(_builds(*lanes))
    assert build is not None
    assert build.build_id == expected


def test_no_builds_means_no_recommendation() -> None:
    assert recommend_build(()) is None


def test_only_cuda_inside_a_sandbox_needs_device_access() -> None:
    (cuda, vulkan) = _builds(
        ("cuda", (Accelerator.CUDA, Accelerator.CPU)),
        ("vulkan", (Accelerator.VULKAN, Accelerator.CPU)),
    )
    assert needs_device_access(cuda, sandboxed=True)
    assert not needs_device_access(cuda, sandboxed=False)
    assert not needs_device_access(vulkan, sandboxed=True)


def test_the_shipped_catalog_recommends_vulkan_on_linux() -> None:
    engine = load_catalog().engine("transcribe-cpp")
    assert engine is not None
    build = recommend_build(engine.builds_for(LINUX))
    assert build is not None
    assert build.build_id == "linux-x86_64-cpu-vulkan"
