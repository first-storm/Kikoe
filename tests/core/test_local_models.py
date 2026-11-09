"""Engine catalog validation, merging and build selection without I/O."""

import copy
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from kikoe.core.local_models import (
    Accelerator,
    Architecture,
    ArchiveKind,
    EngineBuild,
    LocalCatalogError,
    LocalModelSpec,
    ModelRole,
    OperatingSystem,
    Platform,
    group_by_family,
    merge_catalogs,
    model_spec,
    needs_device_access,
    parse_engine_catalog,
    recommend_build,
)

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
    "required-language-flag": lambda d: _model(d).update(requires_language="yes"),
    "native-diarization-flag": lambda d: _model(d).update(native_diarization="yes"),
    "flag": lambda d: _model(d).update(timestamps="yes"),
    "file-and-files": lambda d: _model(d).update(files=[_model(d)["file"]]),
    "no-file": lambda d: _model(d).pop("file"),
    "empty-files": lambda d: _model(d).update(files=[]) or _model(d).pop("file"),
    "nested-escape": lambda d: _with_files(d, target="org/../escape"),
    "dot-folder": lambda d: _with_files(d, target="org/./repo"),
    "absolute-target": lambda d: _with_files(d, target="/org/repo"),
}


def _with_files(doc: dict[str, Any], **change: str) -> None:
    model = _model(doc)
    model["files"] = [{**model.pop("file"), **change}]


def test_a_folder_model_keeps_nested_targets_and_sums_its_sizes() -> None:
    doc = copy.deepcopy(document())
    model = _model(doc)
    first = model.pop("file")
    second = {**first, "url": "https://example.test/weight.bin", "size": 5}
    model["files"] = [
        {**first, "target": "qwen3-speech/models/aufklarer/Qwen3-ASR-0.6B-MLX-4bit"},
        {**second, "target": "qwen3-speech/models/aufklarer/X/encoder.mlmodelc/weights"},
    ]
    (spec,) = parse_engine_catalog(doc)[1]
    assert spec.folder
    assert spec.size == first["size"] + 5
    assert spec.files[1].target.endswith("encoder.mlmodelc/weights")


def test_a_single_file_model_is_not_a_folder() -> None:
    (spec,) = parse_engine_catalog(document())[1]
    assert not spec.folder
    assert spec.files == (spec.file,)


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


def test_older_catalog_entries_default_to_external_speaker_detection() -> None:
    _, models = parse_engine_catalog(document())
    assert not models[0].native_diarization
    capabilities = model_spec(models[0]).capabilities.file
    assert capabilities is not None
    assert not capabilities.diarization


def _spec(model_id: str, family: str, role: ModelRole = ModelRole.ASR) -> LocalModelSpec:
    _, models = parse_engine_catalog(document())
    return replace(models[0], model_id=model_id, family=family, role=role)


def test_families_keep_catalog_order_and_leave_out_speaker_detection() -> None:
    models = (
        _spec("whisper-tiny", "Whisper"),
        _spec("parakeet", "Parakeet"),
        _spec("sortformer", "Speaker Detection", ModelRole.DIARIZE),
        _spec("whisper-base", "Whisper"),
    )
    families = group_by_family(models)
    assert [f.name for f in families] == ["Whisper", "Parakeet"]
    assert [m.model_id for m in families[0].models] == ["whisper-tiny", "whisper-base"]


def test_no_models_means_no_families() -> None:
    assert group_by_family(()) == ()
