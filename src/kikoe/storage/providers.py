"""The set of configured providers: settings file for details, keyring for API keys."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import replace

from kikoe.core.models import ApiKind, ModelSpec, Provider
from kikoe.storage.secrets import SecretStore
from kikoe.storage.settings import SettingsStore

log = logging.getLogger(__name__)


class ProviderNotFoundError(LookupError):
    """A provider or model was removed while something still referred to it."""


class ProviderRepository:
    """In-memory provider list that persists every change before notifying listeners."""

    def __init__(self, settings: SettingsStore, secrets: SecretStore) -> None:
        self._settings = settings
        self._secrets = secrets
        self._providers: tuple[Provider, ...] = ()
        self._listeners: list[Callable[[], None]] = []

    @property
    def providers(self) -> tuple[Provider, ...]:
        return self._providers

    def find(self, provider_id: str) -> Provider | None:
        return next((p for p in self._providers if p.provider_id == provider_id), None)

    def get(self, provider_id: str) -> Provider:
        provider = self.find(provider_id)
        if provider is None:
            raise ProviderNotFoundError(f"Provider {provider_id} no longer exists.")
        return provider

    def connect_changed(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after every change, including the initial load.

        Returns a function that stops the calls, for short-lived listeners such as pages.
        """
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def load(self) -> None:
        self._providers = await asyncio.to_thread(self._settings.load)
        self._notify()

    async def get_api_key(self, provider_id: str) -> str:
        return await asyncio.to_thread(self._secrets.get, provider_id) or ""

    async def add_provider(self, name: str, api: ApiKind, base_url: str, api_key: str) -> Provider:
        provider = Provider(uuid.uuid4().hex, name.strip(), api, base_url)
        if api_key:
            await asyncio.to_thread(self._secrets.set, provider.provider_id, api_key)
        await self._commit((*self._providers, provider))
        return provider

    async def update_provider(
        self, provider_id: str, name: str, api: ApiKind, base_url: str, api_key: str
    ) -> None:
        """Replace connection details; an empty ``api_key`` removes the stored key."""
        current = self.get(provider_id)
        if api_key:
            await asyncio.to_thread(self._secrets.set, provider_id, api_key)
        else:
            await asyncio.to_thread(self._secrets.delete, provider_id)
        updated = replace(current, name=name.strip(), api=api, base_url=base_url)
        await self._commit(self._replace_provider(updated))

    async def remove_provider(self, provider_id: str) -> None:
        self.get(provider_id)
        await asyncio.to_thread(self._secrets.delete, provider_id)
        await self._commit(tuple(p for p in self._providers if p.provider_id != provider_id))

    async def save_model(
        self, provider_id: str, model: ModelSpec, replacing_id: str | None = None
    ) -> None:
        """Add ``model``, or replace the model whose ID is ``replacing_id`` keeping its position."""
        provider = self.get(provider_id)
        if replacing_id is None:
            models = (*provider.models, model)
        else:
            if all(m.model_id != replacing_id for m in provider.models):
                raise ProviderNotFoundError(f"Model {replacing_id} no longer exists.")
            models = tuple(model if m.model_id == replacing_id else m for m in provider.models)
        await self._commit(self._replace_provider(replace(provider, models=models)))

    async def remove_model(self, provider_id: str, model_id: str) -> None:
        provider = self.get(provider_id)
        models = tuple(m for m in provider.models if m.model_id != model_id)
        await self._commit(self._replace_provider(replace(provider, models=models)))

    def _replace_provider(self, updated: Provider) -> tuple[Provider, ...]:
        return tuple(
            updated if p.provider_id == updated.provider_id else p for p in self._providers
        )

    async def _commit(self, providers: tuple[Provider, ...]) -> None:
        await asyncio.to_thread(self._settings.save, providers)
        self._providers = providers
        self._notify()

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()
