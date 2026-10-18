"""Downloads, installs and removals of local engines and models, against fake servers."""

import asyncio
import hashlib
import io
import tarfile
import time
import zipfile
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from kikoe.core.local_models import (
    Accelerator,
    Architecture,
    ArchiveKind,
    Download,
    EngineBuild,
    EngineDevice,
    EngineProbe,
    EngineSpec,
    InstallStatus,
    LocalCatalog,
    LocalModelSpec,
    LocalSettings,
    ModelRole,
    OperatingSystem,
    Platform,
)
from kikoe.providers.base import TranscriptionError
from kikoe.providers.local.connection import EngineConnection
from kikoe.services.local_models import LocalModelManager
from kikoe.storage.downloads import DownloadError, download
from kikoe.storage.local_installs import LocalInstalls, safe_member
from kikoe.storage.local_settings import LocalSettingsStore
from tests.helpers import run_async

LINUX = Platform(OperatingSystem.LINUX, Architecture.X86_64)


def _tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class Server:
    """Serves fixed bodies by URL and records requests."""

    def __init__(self) -> None:
        self.bodies: dict[str, bytes] = {}
        self.status = 200

    def add(
        self,
        name: str,
        body: bytes,
        archive: ArchiveKind = ArchiveKind.FILE,
        *,
        target: str = "",
        strip: int = 0,
    ) -> Download:
        url = f"https://files.test/{name}"
        self.bodies[url] = body
        return Download(url, hashlib.sha256(body).hexdigest(), len(body), archive, target, strip)

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            body = self.bodies.get(str(request.url))
            if body is None:
                return httpx.Response(404)
            return httpx.Response(self.status, content=body)

        return httpx.MockTransport(handle)


def _settings(tmp_path: Path) -> LocalSettingsStore:
    return LocalSettingsStore(tmp_path / "local.json")


def _ignore(_received: int) -> None:
    pass


def _entries(folder: Path) -> list[Path]:
    return sorted(folder.iterdir())


@run_async
async def test_a_verified_download_is_moved_into_place(tmp_path: Path) -> None:
    server = Server()
    item = server.add("model.gguf", b"weights")
    received: list[int] = []
    await download(item, tmp_path / "model.gguf", received.append, transport=server.transport())
    assert (tmp_path / "model.gguf").read_bytes() == b"weights"
    assert sum(received) == len(b"weights")
    assert _entries(tmp_path) == [tmp_path / "model.gguf"]


@pytest.mark.parametrize("problem", ["checksum", "status", "short", "long"])
@run_async
async def test_a_bad_download_leaves_nothing_behind(tmp_path: Path, problem: str) -> None:
    server = Server()
    item = server.add("model.gguf", b"weights")
    match problem:
        case "checksum":
            server.bodies[item.url] = b"tampers"
        case "status":
            server.status = 500
        case "short":
            server.bodies[item.url] = b"weigh"
        case _:
            server.bodies[item.url] = b"weights and more"
    with pytest.raises(DownloadError):
        await download(item, tmp_path / "model.gguf", _ignore, transport=server.transport())
    assert _entries(tmp_path) == []


@run_async
async def test_plain_http_is_refused(tmp_path: Path) -> None:
    item = Download("http://files.test/model.gguf", "a" * 64, 1)
    with pytest.raises(DownloadError):
        await download(item, tmp_path / "model.gguf", _ignore, transport=Server().transport())


@pytest.mark.parametrize("name", ["/etc/passwd", "../outside", "a/../../outside", "C:\\evil"])
def test_unsafe_archive_paths_are_refused(name: str) -> None:
    with pytest.raises(DownloadError):
        safe_member(name, 0)


def test_archive_members_lose_their_leading_folders() -> None:
    assert str(safe_member("top/lib/x.so", 1)) == "lib/x.so"
    assert safe_member("top/", 1) is None


def _engine(server: Server, native: bytes | None = None) -> EngineSpec:
    binding = server.add(
        "binding.whl", _zip({"pkg/__init__.py": b"x = 1"}), ArchiveKind.ZIP, target="python"
    )
    library = server.add(
        "native.tar.gz",
        native or _tar({"top/libengine.so": b"ELF", "top/licenses/LICENSE": b"MIT"}),
        ArchiveKind.TAR_GZ,
        target="native",
        strip=1,
    )
    builds = tuple(
        EngineBuild(build_id, LINUX, accelerators, (library,))
        for build_id, accelerators in (
            ("gpu", (Accelerator.VULKAN, Accelerator.CPU)),
            ("cpu", (Accelerator.CPU,)),
        )
    )
    return EngineSpec("engine", "Engine", "1.0", "https://engine.test", (binding,), builds)


def _model(server: Server, model_id: str = "tiny") -> LocalModelSpec:
    file = server.add(f"{model_id}.gguf", f"{model_id} weights".encode())
    return LocalModelSpec(model_id, "engine", model_id.title(), "Family", ModelRole.ASR, file, ())


def _manager(
    tmp_path: Path, server: Server, models: tuple[LocalModelSpec, ...] = ()
) -> LocalModelManager:
    catalog = LocalCatalog((_engine(server),), models)
    return LocalModelManager(
        catalog, LINUX, LocalInstalls(tmp_path, server.transport()), _settings(tmp_path)
    )


@run_async
async def test_installing_an_engine_unpacks_every_component(tmp_path: Path) -> None:
    server = Server()
    manager = _manager(tmp_path, server)
    await manager.load()
    await manager.install_engine("engine", "gpu")
    folder = tmp_path / "engines" / "engine"
    assert (folder / "python" / "pkg" / "__init__.py").read_bytes() == b"x = 1"
    assert (folder / "native" / "libengine.so").read_bytes() == b"ELF"
    assert manager.engine_state("engine").status is InstallStatus.INSTALLED
    assert manager.installed_build("engine") == manager.catalog.engines[0].build("gpu")
    assert _entries(tmp_path / "staging") == []


@run_async
async def test_installed_state_survives_a_restart(tmp_path: Path) -> None:
    server = Server()
    model = _model(server)
    first = _manager(tmp_path, server, (model,))
    await first.install_engine("engine", "cpu")
    await first.install_model("tiny")
    second = _manager(tmp_path, server, (model,))
    await second.load()
    assert second.engine_state("engine").status is InstallStatus.INSTALLED
    assert second.installed_build("engine") == second.catalog.engines[0].build("cpu")
    assert second.model_state("tiny").status is InstallStatus.INSTALLED


@run_async
async def test_switching_builds_replaces_the_old_one(tmp_path: Path) -> None:
    server = Server()
    manager = _manager(tmp_path, server)
    await manager.install_engine("engine", "gpu")
    await manager.install_engine("engine", "cpu")
    assert manager.installed_build("engine") == manager.catalog.engines[0].build("cpu")
    assert [p.name for p in _entries(tmp_path / "engines")] == ["engine"]


@run_async
async def test_a_failed_reinstall_keeps_the_working_engine(tmp_path: Path) -> None:
    server = Server()
    manager = _manager(tmp_path, server)
    await manager.install_engine("engine", "gpu")
    server.status = 503
    with pytest.raises(DownloadError):
        await manager.install_engine("engine", "cpu")
    assert manager.installed_build("engine") == manager.catalog.engines[0].build("gpu")
    assert (tmp_path / "engines" / "engine" / "native" / "libengine.so").is_file()


@run_async
async def test_an_archive_escaping_its_folder_is_not_installed(tmp_path: Path) -> None:
    server = Server()
    catalog = LocalCatalog((_engine(server, _tar({"top/../../evil": b"x"})),))
    manager = LocalModelManager(
        catalog, LINUX, LocalInstalls(tmp_path / "root", server.transport()), _settings(tmp_path)
    )
    with pytest.raises(DownloadError):
        await manager.install_engine("engine", "gpu")
    assert not (tmp_path / "evil").exists()
    assert manager.engine_state("engine").status is InstallStatus.MISSING


@run_async
async def test_cancelling_a_download_cleans_up(tmp_path: Path) -> None:
    server = Server()
    model = _model(server)
    gate = asyncio.Event()

    def slow(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Slow(server.bodies[str(request.url)], gate))

    manager = LocalModelManager(
        LocalCatalog((_engine(server),), (model,)),
        LINUX,
        LocalInstalls(tmp_path, httpx.MockTransport(slow)),
        _settings(tmp_path),
    )
    task = asyncio.create_task(manager.install_model("tiny"))
    await gate.wait()
    assert manager.model_state("tiny").status is InstallStatus.DOWNLOADING
    manager.cancel("tiny")
    with pytest.raises(asyncio.CancelledError):
        await task
    assert manager.model_state("tiny").status is InstallStatus.MISSING
    assert _entries(tmp_path / "staging") == []
    assert not (tmp_path / "models").exists()


class _Slow(httpx.AsyncByteStream):
    """Sends one byte, then waits forever, so the test can cancel mid-download."""

    def __init__(self, body: bytes, started: asyncio.Event) -> None:
        self._body, self._started = body, started

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body[:1]
        self._started.set()
        await asyncio.Event().wait()


@run_async
async def test_removing_an_engine_can_keep_or_remove_its_models(tmp_path: Path) -> None:
    server = Server()
    models = (_model(server, "tiny"), _model(server, "base"))
    manager = _manager(tmp_path, server, models)
    await manager.install_engine("engine", "gpu")
    await manager.install_model("tiny")
    await manager.remove_engine("engine", with_models=False)
    assert manager.engine_state("engine").status is InstallStatus.MISSING
    assert [m.model_id for m in manager.installed_models("engine")] == ["tiny"]
    await manager.install_engine("engine", "gpu")
    await manager.remove_engine("engine", with_models=True)
    assert manager.installed_models() == ()
    assert _entries(tmp_path / "models") == []


@run_async
async def test_a_model_with_a_changed_checksum_counts_as_missing(tmp_path: Path) -> None:
    server = Server()
    model = _model(server)
    manager = _manager(tmp_path, server, (model,))
    await manager.install_model("tiny")
    newer = LocalModelSpec(
        "tiny", "engine", "Tiny", "Family", ModelRole.ASR, server.add("tiny2.gguf", b"new"), ()
    )
    restarted = _manager(tmp_path, server, (newer,))
    await restarted.load()
    assert restarted.model_state("tiny").status is InstallStatus.MISSING


@run_async
async def test_listeners_hear_about_downloads(tmp_path: Path) -> None:
    server = Server()
    manager = _manager(tmp_path, server, (_model(server),))
    states: list[InstallStatus] = []
    record: Callable[[], None] = lambda: states.append(manager.model_state("tiny").status)  # noqa: E731
    manager.connect_changed(record)
    await manager.install_model("tiny")
    assert states[0] is InstallStatus.DOWNLOADING
    assert states[-1] is InstallStatus.INSTALLED


def test_engines_without_a_build_for_this_computer_are_hidden(tmp_path: Path) -> None:
    server = Server()
    other = Platform(OperatingSystem.WINDOWS, Architecture.ARM64)
    manager = LocalModelManager(
        LocalCatalog((_engine(server),), (_model(server),)),
        other,
        LocalInstalls(tmp_path),
        _settings(tmp_path),
    )
    assert manager.engines == ()
    assert manager.models == ()


class FakeConnection(EngineConnection):
    def __init__(self, result: EngineProbe | TranscriptionError) -> None:
        self.result = result
        self.closed = False

    async def probe(self) -> EngineProbe:
        if isinstance(self.result, TranscriptionError):
            raise self.result
        return self.result

    async def close(self) -> None:
        self.closed = True


@run_async
async def test_probing_keeps_the_devices_or_the_failure(tmp_path: Path) -> None:
    server = Server()
    probe = EngineProbe("1.0", (EngineDevice("vulkan:1", "vulkan", "GPU"),))
    connections: list[FakeConnection] = []

    def connect(_engine_id: str, _folder: Path) -> EngineConnection:
        connections.append(FakeConnection(probe if not connections else TranscriptionError("x")))
        return connections[-1]

    manager = LocalModelManager(
        LocalCatalog((_engine(server),)),
        LINUX,
        LocalInstalls(tmp_path, server.transport()),
        _settings(tmp_path),
        connect=connect,
    )
    await manager.probe("engine")
    assert manager.probe_result("engine") is None
    await manager.install_engine("engine", "gpu")
    await manager.probe("engine")
    assert manager.probe_result("engine") == probe
    await manager.install_engine("engine", "cpu")
    assert connections[0].closed
    assert manager.probe_result("engine") is None
    await manager.probe("engine")
    assert isinstance(manager.probe_result("engine"), TranscriptionError)


@run_async
async def test_the_chosen_device_is_remembered(tmp_path: Path) -> None:
    server = Server()
    manager = _manager(tmp_path, server)
    await manager.load()
    assert manager.device("engine") is None
    await manager.set_device("engine", "vulkan:1")
    restarted = _manager(tmp_path, server)
    await restarted.load()
    assert restarted.device("engine") == "vulkan:1"


@run_async
async def test_overlapping_device_changes_are_all_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = Server()
    manager = _manager(tmp_path, server)
    await manager.load()
    store = manager._settings_store
    save = store.save

    def slow_save(settings: LocalSettings) -> None:
        time.sleep(0.05)  # Keeps two saves overlapping unless the manager serializes them.
        save(settings)

    monkeypatch.setattr(store, "save", slow_save)
    await asyncio.gather(
        manager.set_device("engine", "cpu:0"), manager.set_device("other", "vulkan:1")
    )
    restarted = _manager(tmp_path, server)
    await restarted.load()
    assert (restarted.device("engine"), restarted.device("other")) == ("cpu:0", "vulkan:1")


def test_an_engine_that_is_not_installed_has_no_connection(tmp_path: Path) -> None:
    manager = _manager(tmp_path, Server())
    with pytest.raises(TranscriptionError):
        manager.connection("engine")


@run_async
async def test_only_models_of_an_installed_engine_are_offered(tmp_path: Path) -> None:
    server = Server()
    manager = _manager(tmp_path, server, (_model(server, "tiny"), _model(server, "base")))
    await manager.install_model("tiny")
    assert manager.providers == ()
    await manager.install_engine("engine", "gpu")
    (provider,) = manager.providers
    assert [m.model_id for m in provider.models] == ["tiny"]
    assert provider.upload.parallelism == 1
    found = manager.installed_model("tiny")
    assert found is not None and found[1] == tmp_path / "models" / "tiny" / "tiny.gguf"
    assert manager.installed_model("base") is None


@run_async
async def test_speaker_detection_is_offered_once_its_model_is_installed(tmp_path: Path) -> None:
    server = Server()
    timed = replace(_model(server, "tiny"), timestamps=True)
    untimed = _model(server, "base")
    speakers = replace(_model(server, "speakers"), role=ModelRole.DIARIZE)
    manager = _manager(tmp_path, server, (timed, untimed, speakers))
    await manager.install_engine("engine", "gpu")
    for model_id in ("tiny", "base"):
        await manager.install_model(model_id)

    def diarization() -> dict[str, bool]:
        (provider,) = manager.providers
        return {
            m.model_id: bool(m.capabilities.file and m.capabilities.file.diarization)
            for m in provider.models
        }

    assert diarization() == {"tiny": False, "base": False}
    await manager.install_model("speakers")
    assert diarization() == {"tiny": True, "base": False}
    assert manager.diarizer("engine") == tmp_path / "models" / "speakers" / "speakers.gguf"


@run_async
async def test_native_speaker_detection_survives_external_diarizer_removal(tmp_path: Path) -> None:
    server = Server()
    native = replace(_model(server, "moss"), timestamps=True, native_diarization=True)
    speakers = replace(_model(server, "speakers"), role=ModelRole.DIARIZE)
    manager = _manager(tmp_path, server, (native, speakers))
    await manager.install_engine("engine", "gpu")
    await manager.install_model("moss")
    for install_speakers in (True, False):
        (provider,) = manager.providers
        assert provider.models[0].capabilities.file is not None
        assert provider.models[0].capabilities.file.diarization
        if install_speakers:
            await manager.install_model("speakers")
        else:
            await manager.remove_model("speakers")
    (provider,) = manager.providers
    assert provider.models[0].capabilities.file is not None
    assert provider.models[0].capabilities.file.diarization


@run_async
async def test_a_bundled_engine_is_used_and_cannot_be_removed_or_replaced(tmp_path: Path) -> None:
    server = Server()
    builder = _manager(tmp_path / "build", server)
    await builder.install_engine("engine", "gpu")
    shipped = tmp_path / "app" / "local-engines"
    shipped.mkdir(parents=True)
    (tmp_path / "build" / "engines" / "engine").rename(shipped / "engine")
    catalog = LocalCatalog((_engine(server),), (_model(server),))
    manager = LocalModelManager(
        catalog,
        LINUX,
        LocalInstalls(tmp_path / "user", server.transport(), bundled=shipped),
        _settings(tmp_path),
    )
    await manager.load()
    assert manager.engine_state("engine").status is InstallStatus.BUNDLED
    assert manager.installed_build("engine") == catalog.engines[0].build("gpu")
    await manager.install_model("tiny")
    assert manager.providers
    with pytest.raises(ValueError, match="bundled"):
        await manager.remove_engine("engine", with_models=False)
    with pytest.raises(ValueError, match="bundled"):
        await manager.install_engine("engine", "cpu")
    assert (shipped / "engine" / "install.json").is_file()
