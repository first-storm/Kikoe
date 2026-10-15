"""Local engines and models: what can be installed here, what is, and what is downloading."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from kikoe.core.listeners import Listeners
from kikoe.core.local_models import (
    EngineBuild,
    EngineProbe,
    EngineSpec,
    InstallState,
    InstallStatus,
    LocalCatalog,
    LocalModelSpec,
    LocalSettings,
    Platform,
    recommend_build,
)
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError
from kikoe.providers.local.connection import EngineConnection
from kikoe.providers.local.worker_host import WorkerHost, worker_command
from kikoe.storage.local_installs import EngineReceipt, LocalInstalls
from kikoe.storage.local_settings import LocalSettingsStore

log = logging.getLogger(__name__)

_PROGRESS_INTERVAL = 0.1
"""Seconds between progress notifications, so a fast download does not flood the UI."""


type ConnectionFactory = Callable[[str, Path], EngineConnection]


def start_worker(engine_id: str, engine_dir: Path) -> EngineConnection:
    """A connection whose worker process starts on its first request."""
    return EngineConnection(WorkerHost(worker_command(engine_id, engine_dir)))


@dataclass(slots=True)
class _Job:
    task: asyncio.Task[object] | asyncio.Task[None]
    total: int
    received: int = 0


class LocalModelManager:
    """The one place that knows the state of local engines and models; screens observe it.

    Each engine and model has at most one download at a time. Downloads run in the
    caller's task, so the caller reports failures; :meth:`cancel` stops them.
    """

    def __init__(
        self,
        catalog: LocalCatalog,
        platform: Platform | None,
        installs: LocalInstalls,
        settings: LocalSettingsStore,
        *,
        sandboxed: bool = False,
        connect: ConnectionFactory = start_worker,
    ) -> None:
        self.catalog = catalog
        self.platform = platform
        self.sandboxed = sandboxed
        """Running in Flatpak, where CUDA needs extra device access."""
        self._installs = installs
        self._listeners = Listeners[()]()
        self._engines: dict[str, EngineReceipt] = {}
        self._models: set[str] = set()
        self._jobs: dict[str, _Job] = {}
        """Keyed by engine or model ID; the catalog keeps the two sets of IDs apart."""
        self._last_emit = 0.0
        self._settings_store = settings
        self._settings = LocalSettings()
        self._connect = connect
        self._connections: dict[str, EngineConnection] = {}
        self._probes: dict[str, EngineProbe | TranscriptionError] = {}
        self._probing: set[str] = set()

    def connect_changed(self, listener: Callable[[], None]) -> Callable[[], None]:
        return self._listeners.connect(listener)

    async def load(self) -> None:
        """Read what is installed and discard leftovers of interrupted downloads."""
        await asyncio.to_thread(self._installs.clean_staging)
        self._engines, self._models = await asyncio.to_thread(self._scan)
        self._settings = await asyncio.to_thread(self._settings_store.load)
        self._listeners.emit()

    async def close(self) -> None:
        """Stop every worker, e.g. when the application quits."""
        for engine_id in tuple(self._connections):
            await self._disconnect(engine_id)

    def connection(self, engine_id: str) -> EngineConnection:
        """The worker of an installed engine, started on its first request."""
        receipt = self._engines.get(engine_id)
        if receipt is None:
            raise TranscriptionError(
                _("Download the local engine in Preferences to use this model.")
            )
        connection = self._connections.get(engine_id)
        if connection is None:
            connection = self._connect(engine_id, receipt.path)
            self._connections[engine_id] = connection
        return connection

    def probe_result(self, engine_id: str) -> EngineProbe | TranscriptionError | None:
        """What the running engine reported; None until it has been asked."""
        return self._probes.get(engine_id)

    async def probe(self, engine_id: str) -> None:
        """Start the engine and ask what it can run on; failures are kept for display."""
        if engine_id in self._probing or engine_id not in self._engines:
            return
        self._probing.add(engine_id)
        try:
            result: EngineProbe | TranscriptionError = await self.connection(engine_id).probe()
        except TranscriptionError as error:
            result = error
        finally:
            self._probing.discard(engine_id)
        if engine_id in self._engines:
            self._probes[engine_id] = result
            self._listeners.emit()

    def device(self, engine_id: str) -> str | None:
        """The chosen device ID; None picks the fastest one automatically."""
        return self._settings.device(engine_id)

    async def set_device(self, engine_id: str, device_id: str | None) -> None:
        settings = self._settings.with_device(engine_id, device_id)
        await asyncio.to_thread(self._settings_store.save, settings)
        self._settings = settings
        self._listeners.emit()

    async def _disconnect(self, engine_id: str) -> None:
        """A replaced or removed engine must not keep its old library loaded."""
        self._probes.pop(engine_id, None)
        connection = self._connections.pop(engine_id, None)
        if connection is not None:
            await connection.close()

    def _scan(self) -> tuple[dict[str, EngineReceipt], set[str]]:
        engines = {
            e.engine_id: r
            for e in self.engines
            if (r := self._installs.engine_receipt(e.engine_id)) is not None
        }
        models = {m.model_id for m in self.models if self._installs.model_installed(m)}
        return engines, models

    @property
    def engines(self) -> tuple[EngineSpec, ...]:
        """Engines with a build for this computer, in registry order."""
        platform = self.platform
        if platform is None:
            return ()
        return tuple(e for e in self.catalog.engines if e.builds_for(platform))

    @property
    def models(self) -> tuple[LocalModelSpec, ...]:
        """Models of the engines available here."""
        engines = {e.engine_id for e in self.engines}
        return tuple(m for m in self.catalog.models if m.engine_id in engines)

    def builds(self, engine: EngineSpec) -> tuple[EngineBuild, ...]:
        return engine.builds_for(self.platform) if self.platform else ()

    def recommended_build(self, engine: EngineSpec) -> EngineBuild | None:
        return recommend_build(self.builds(engine))

    def installed_build(self, engine_id: str) -> EngineBuild | None:
        receipt = self._engines.get(engine_id)
        engine = self.catalog.engine(engine_id)
        if receipt is None or engine is None:
            return None
        return engine.build(receipt.build_id)

    def engine_state(self, engine_id: str) -> InstallState:
        return self._state(engine_id, engine_id in self._engines)

    def model_state(self, model_id: str) -> InstallState:
        return self._state(model_id, model_id in self._models)

    def installed_models(self, engine_id: str | None = None) -> tuple[LocalModelSpec, ...]:
        return tuple(
            m
            for m in self.models
            if m.model_id in self._models and engine_id in (None, m.engine_id)
        )

    def _state(self, key: str, installed: bool) -> InstallState:
        job = self._jobs.get(key)
        if job is not None:
            return InstallState(InstallStatus.DOWNLOADING, job.received, job.total)
        return InstallState(InstallStatus.INSTALLED if installed else InstallStatus.MISSING)

    async def install_engine(self, engine_id: str, build_id: str) -> None:
        """Install ``build_id``, replacing another installed build of the engine."""
        engine = self.catalog.engine(engine_id)
        build = engine.build(build_id) if engine else None
        if engine is None or build is None or build not in self.builds(engine):
            raise ValueError(f"no build {build_id} of {engine_id} for this computer")
        total = sum(d.size for d in engine.downloads(build))
        async with self._job(engine_id, total) as progress:
            await self._disconnect(engine_id)
            await self._installs.install_engine(engine, build, progress)
            receipt = await asyncio.to_thread(self._installs.engine_receipt, engine_id)
            if receipt is not None:
                self._engines[engine_id] = receipt

    async def install_model(self, model_id: str) -> None:
        model = self.catalog.model(model_id)
        if model is None:
            raise ValueError(f"unknown model {model_id}")
        async with self._job(model_id, model.size) as progress:
            await self._installs.install_model(model, progress)
            self._models.add(model_id)

    def cancel(self, key: str) -> None:
        job = self._jobs.get(key)
        if job is not None:
            job.task.cancel()

    async def remove_engine(self, engine_id: str, *, with_models: bool) -> None:
        """Remove the engine; its models stay unless ``with_models`` is set."""
        self.cancel(engine_id)
        await self._disconnect(engine_id)
        await asyncio.to_thread(self._installs.remove_engine, engine_id)
        self._engines.pop(engine_id, None)
        if with_models:
            for model in self.installed_models(engine_id):
                await self._remove_model(model.model_id)
        self._listeners.emit()

    async def remove_model(self, model_id: str) -> None:
        self.cancel(model_id)
        await self._remove_model(model_id)
        self._listeners.emit()

    async def _remove_model(self, model_id: str) -> None:
        await asyncio.to_thread(self._installs.remove_model, model_id)
        self._models.discard(model_id)

    def model_path(self, model: LocalModelSpec) -> str:
        return str(self._installs.model_path(model))

    @asynccontextmanager
    async def _job(self, key: str, total: int) -> AsyncGenerator[Callable[[int], None]]:
        """Register the running task as the download of ``key`` while the block runs."""
        if key in self._jobs:
            raise RuntimeError(f"{key} is already downloading")
        task = asyncio.current_task()
        assert task is not None
        job = _Job(task, total)
        self._jobs[key] = job
        self._listeners.emit()
        try:
            yield lambda received: self._progress(job, received)
        finally:
            del self._jobs[key]
            self._listeners.emit()

    def _progress(self, job: _Job, received: int) -> None:
        job.received += received
        now = time.monotonic()
        if now - self._last_emit >= _PROGRESS_INTERVAL:
            self._last_emit = now
            self._listeners.emit()
