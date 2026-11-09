"""Regenerate the models of the speech-swift engine catalog from Hugging Face.

Usage:
    uv run scripts/speech_swift_catalog.py          # rewrite the catalog's "models"
    uv run scripts/speech_swift_catalog.py --check  # fail if it no longer matches

speech-swift downloads each model as a set of files from one Hugging Face repository,
choosing them with glob patterns. Kikoe downloads the same files itself, pinned to the
repository's current commit with their SHA-256, and lays them out where speech-swift
looks for them (``qwen3-speech/models/<org>/<repo>/``). The patterns below mirror each
model's ``downloadWeights(additionalFiles:)`` call in speech-swift; update them together
with the engine version. The engine block of the catalog is edited by hand.
"""

import argparse
import fnmatch
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
CATALOG = (
    ROOT / "src" / "kikoe" / "providers" / "local" / "engines" / "speech_swift" / "catalog.json"
)
HUB = "https://huggingface.co"
CACHE_ROOT = "qwen3-speech/models"
"""speech-swift's cache layout below ``QWEN3_CACHE_DIR``."""

_ALWAYS = ("config.json", "*.safetensors", "model.safetensors.index.json")
"""What ``HuggingFaceDownloader.downloadWeights`` adds to every model's own patterns."""
_TRANSDUCER = ("encoder.mlmodelc/**", "decoder.mlmodelc/**", "joint.mlmodelc/**", "vocab.json")

QWEN3_LANGUAGES = (
    "zh en yue ar de fr es pt id it ko ru th vi ja tr hi ms nl sv da fi pl cs fil fa el ro hu mk"
)
PARAKEET_LANGUAGES = "bg cs da de el en es et fi fr hr hu it lt lv mt nl pl pt ro ru sk sl sv uk"
NEMOTRON_LANGUAGES = (
    "af am ar ay az bg bn cs da de el en es et fa fi fr gn gu ha haw he hi hr hu hy id ig it "
    "ja ka km kn ko ku ky ln lt lv mi ml mr ms mt nah nb ne nl nn no ny or pl pt qu ro ru rw "
    "si sk sl sm so sv sw ta te tg th to tr uk ur uz vi yo zh zu"
)


@dataclass(frozen=True, slots=True)
class Model:
    model_id: str
    name: str
    family: str
    repo: str
    patterns: tuple[str, ...]
    languages: str = ""
    max_seconds: int | None = None
    extra: dict[str, Any] = field(default_factory=dict[str, Any])


MODELS = (
    Model(
        "speech-swift-qwen3-asr-0.6b",
        "Qwen3-ASR 0.6B (4-bit)",
        "Qwen3-ASR",
        "aufklarer/Qwen3-ASR-0.6B-MLX-4bit",
        ("vocab.json", "merges.txt", "tokenizer_config.json"),
        QWEN3_LANGUAGES,
        600,
    ),
    Model(
        "speech-swift-qwen3-asr-1.7b",
        "Qwen3-ASR 1.7B (8-bit)",
        "Qwen3-ASR",
        "aufklarer/Qwen3-ASR-1.7B-MLX-8bit",
        ("vocab.json", "merges.txt", "tokenizer_config.json"),
        QWEN3_LANGUAGES,
        600,
    ),
    Model(
        "speech-swift-parakeet-tdt-0.6b-v3",
        "Parakeet TDT 0.6B v3",
        "Parakeet",
        "aufklarer/Parakeet-TDT-v3-CoreML-INT8-30s",
        _TRANSDUCER,
        PARAKEET_LANGUAGES,
        30,
    ),
    Model(
        "speech-swift-parakeet-eou-120m",
        "Parakeet EOU 120M",
        "Parakeet",
        "aufklarer/Parakeet-EOU-120M-CoreML-INT8",
        _TRANSDUCER,
        "en",
        600,
    ),
    Model(
        "speech-swift-nemotron-3.5-streaming-0.6b",
        "Nemotron 3.5 Streaming 0.6B",
        "Nemotron",
        "aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-CoreML-INT8",
        (
            *_TRANSDUCER,
            "tokenizer.model",
            "*_tokenizer.model",
            "vocab.txt",
            "*_vocab.txt",
            "languages.json",
        ),
        NEMOTRON_LANGUAGES,
        600,
    ),
    Model(
        "speech-swift-omnilingual-300m",
        "Omnilingual ASR 300M",
        "Omnilingual",
        "aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8-10s",
        ("tokenizer.model", "omnilingual-ctc-300m-int8.mlmodelc/**"),
        "",
        40,
    ),
)


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    for pattern in (*_ALWAYS, *patterns):
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif fnmatch.fnmatchcase(path, pattern):
            return True
    return False


def _entry(client: httpx.Client, model: Model) -> dict[str, Any]:
    info = client.get(f"{HUB}/api/models/{model.repo}").raise_for_status().json()
    commit = info["sha"]
    tree = client.get(
        f"{HUB}/api/models/{model.repo}/tree/{commit}", params={"recursive": "true"}
    ).raise_for_status()
    files: list[dict[str, Any]] = []
    for item in tree.json():
        path = item["path"]
        if item["type"] != "file" or not _matches(path, model.patterns):
            continue
        url = f"{HUB}/{model.repo}/resolve/{commit}/{path}"
        lfs = item.get("lfs")
        if lfs:
            sha256, size = lfs["oid"], lfs["size"]
        else:
            body = client.get(url, follow_redirects=True).raise_for_status().content
            sha256, size = hashlib.sha256(body).hexdigest(), len(body)
        folder, _, _name = f"{CACHE_ROOT}/{model.repo}/{path}".rpartition("/")
        files.append({"url": url, "sha256": sha256, "size": size, "target": folder})
    if not files:
        raise SystemExit(f"no files matched for {model.repo}")
    entry: dict[str, Any] = {
        "id": model.model_id,
        "name": model.name,
        "family": model.family,
        "files": files,
        "streaming": True,
    }
    if model.languages:
        entry["languages"] = model.languages.split()
    if model.max_seconds is not None:
        entry["max_seconds"] = model.max_seconds
    return entry | model.extra


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the speech-swift models.")
    parser.add_argument("--check", action="store_true", help="only compare")
    options = parser.parse_args()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    with httpx.Client(timeout=60) as client:
        models = [_entry(client, m) for m in MODELS]
    if options.check:
        if catalog.get("models") != models:
            print(f"{CATALOG.name} is out of date; run without --check", file=sys.stderr)
            return 1
        return 0
    catalog["models"] = models
    CATALOG.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(models)} models to {CATALOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
