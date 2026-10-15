"""Prepare a local engine for bundling into a frozen build (used for macOS).

Downloads the engine's pinned files for this computer, verifies them and unpacks them
exactly as the app would, into ``<output>/<engine id>``. The spec file then ships that
folder as ``local-engines/<engine id>``, where the app finds it as a bundled engine.

Usage: uv run scripts/bundle_engine.py OUTPUT [ENGINE_ID] [BUILD_ID]
"""

import asyncio
import shutil
import sys
from pathlib import Path

from kikoe.core.local_models import recommend_build
from kikoe.providers.local.engines import load_catalog
from kikoe.providers.local.hardware import current_platform
from kikoe.storage.local_installs import LocalInstalls


async def main(output: Path, engine_id: str, build_id: str | None) -> int:
    engine = load_catalog().engine(engine_id)
    platform = current_platform()
    if engine is None or platform is None:
        print(f"no engine {engine_id} for this platform", file=sys.stderr)
        return 1
    builds = engine.builds_for(platform)
    build = engine.build(build_id) if build_id else recommend_build(builds)
    if build is None or build not in builds:
        print(f"no build {build_id} of {engine_id} for this platform", file=sys.stderr)
        return 1
    work = output / ".work"
    installs = LocalInstalls(work)
    await installs.install_engine(engine, build, lambda _received: None)
    destination = output / engine_id
    shutil.rmtree(destination, ignore_errors=True)
    installs.engine_dir(engine_id).rename(destination)
    shutil.rmtree(work)
    print(f"bundled {engine_id} {build.build_id} into {destination}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    output, *rest = sys.argv[1:]
    engine_id = rest[0] if rest else "transcribe-cpp"
    build_id = rest[1] if len(rest) > 1 else None
    sys.exit(asyncio.run(main(Path(output), engine_id, build_id)))
