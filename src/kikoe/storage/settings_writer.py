"""The single writer of the settings file, shared by everything that stores settings."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace

from kikoe.core.model_catalog import ModelCatalog
from kikoe.core.models import Provider
from kikoe.storage.settings import Settings, SettingsStore

log = logging.getLogger(__name__)


class SettingsWriter:
    """Serializes read-modify-write cycles on one in-memory snapshot of the settings.

    Providers, preferences and the window size live in the same file, so they must share
    one lock; otherwise two quick edits could each write a stale copy of the other's data.
    Failed writes never publish state, and nothing is written until a read has succeeded,
    so a damaged file is never replaced by defaults.
    """

    def __init__(self, store: SettingsStore, catalog: ModelCatalog) -> None:
        self._store = store
        self.catalog = catalog
        """Presets and limits applied to every stored model."""
        self._state = Settings()
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def state(self) -> Settings:
        """The latest successfully read or written settings."""
        return self._state

    def clamp(self, provider: Provider) -> Provider:
        """Keep every model of ``provider`` within the catalog's limits."""
        models = tuple(self.catalog.clamp(provider.api, m) for m in provider.models)
        return provider if models == provider.models else replace(provider, models=models)

    async def load(self) -> None:
        """Read the file again, replacing the snapshot."""
        async with self._lock:
            await self._read()

    async def ensure_loaded(self) -> None:
        """Read the file unless an earlier call already did."""
        async with self._lock:
            await self._ensure_loaded()

    async def update(self, change: Callable[[Settings], Settings]) -> None:
        """Apply ``change`` to the latest snapshot and persist the result."""
        async with self._lock:
            await self._ensure_loaded()
            state = change(self._state)
            await asyncio.to_thread(self._store.save_state, state)
            self._state = state

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self._read()

    async def _read(self) -> None:
        """An app update may tighten a limit, so stored models are clamped again on load."""
        state = await asyncio.to_thread(self._store.load_state)
        self._state = replace(state, providers=tuple(self.clamp(p) for p in state.providers))
        self._loaded = True
