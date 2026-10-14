"""The set of configured providers: settings file for details, keyring for API keys."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import replace

from kikoe.core.model_catalog import ModelCatalog
from kikoe.core.models import ApiKind, ModelSpec, Provider, UploadSettings
from kikoe.core.upload import validate_upload
from kikoe.i18n import _
from kikoe.storage.secrets import SecretStore
from kikoe.storage.settings_writer import SettingsWriter

log = logging.getLogger(__name__)


class ProviderNotFoundError(LookupError):
    """A provider or model was removed while something still referred to it."""


class ProviderRepository:
    """In-memory provider list that persists every change before notifying listeners.

    Every model is kept within the catalog's limits, so the rest of the application can
    trust ``ModelSpec.capabilities`` without knowing about specific endpoints.
    """

    def __init__(self, writer: SettingsWriter, secrets: SecretStore) -> None:
        self._writer = writer
        self._secrets = secrets
        self._listeners: list[Callable[[], None]] = []

    @property
    def providers(self) -> tuple[Provider, ...]:
        return self._writer.state.providers

    @property
    def catalog(self) -> ModelCatalog:
        """Presets and limits that the model dialog offers and saving enforces."""
        return self._writer.catalog

    def find(self, provider_id: str) -> Provider | None:
        return next((p for p in self.providers if p.provider_id == provider_id), None)

    def get(self, provider_id: str) -> Provider:
        provider = self.find(provider_id)
        if provider is None:
            raise ProviderNotFoundError(
                _("Provider {provider_id} no longer exists.").format(provider_id=provider_id)
            )
        return provider

    def connect_changed(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after every change, including the initial load.

        Returns a function that stops the calls, for short-lived listeners such as pages.
        """
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def load(self) -> None:
        await self._writer.load()
        self._notify()

    async def get_api_key(self, provider_id: str) -> str:
        return await asyncio.to_thread(self._secrets.get, provider_id) or ""

    async def add_provider(
        self,
        name: str,
        api: ApiKind,
        base_url: str,
        api_key: str,
        upload: UploadSettings | None = None,
    ) -> Provider:
        upload = upload or UploadSettings()
        validate_upload(upload, api)
        await self._writer.ensure_loaded()
        provider = Provider(uuid.uuid4().hex, name.strip(), api, base_url, upload=upload)
        if api_key:
            await asyncio.to_thread(self._secrets.set, provider.provider_id, api_key)
        await self._commit(lambda: (*self.providers, provider))
        return provider

    async def update_provider(
        self,
        provider_id: str,
        name: str,
        api: ApiKind,
        base_url: str,
        api_key: str,
        *,
        upload: UploadSettings | None = None,
    ) -> None:
        """Replace connection details; an empty ``api_key`` removes the stored key."""
        if upload is not None:
            validate_upload(upload, api)
        self.get(provider_id)
        if api_key:
            await asyncio.to_thread(self._secrets.set, provider_id, api_key)
        else:
            await asyncio.to_thread(self._secrets.delete, provider_id)

        def change() -> tuple[Provider, ...]:
            current = self.get(provider_id)
            return self._replace_provider(
                replace(
                    current,
                    name=name.strip(),
                    api=api,
                    base_url=base_url,
                    upload=upload or (current.upload if current.api == api else UploadSettings()),
                )
            )

        await self._commit(change)

    async def update_upload(self, provider_id: str, upload: UploadSettings) -> None:
        """Merge upload edits under the writer lock without touching credentials or models."""

        def change() -> tuple[Provider, ...]:
            provider = self.get(provider_id)
            validate_upload(upload, provider.api)
            return self._replace_provider(replace(provider, upload=upload))

        await self._commit(change)

    async def remove_provider(self, provider_id: str) -> None:
        self.get(provider_id)
        await asyncio.to_thread(self._secrets.delete, provider_id)
        await self._commit(lambda: tuple(p for p in self.providers if p.provider_id != provider_id))

    async def save_model(
        self, provider_id: str, model: ModelSpec, replacing_id: str | None = None
    ) -> None:
        """Add ``model``, or replace the model whose ID is ``replacing_id`` keeping its position."""

        def change() -> tuple[Provider, ...]:
            provider = self.get(provider_id)
            if replacing_id is None:
                models = (*provider.models, model)
            else:
                if all(m.model_id != replacing_id for m in provider.models):
                    raise ProviderNotFoundError(
                        _("Model {replacing_id} no longer exists.").format(
                            replacing_id=replacing_id
                        )
                    )
                models = tuple(model if m.model_id == replacing_id else m for m in provider.models)
            return self._replace_provider(replace(provider, models=models))

        await self._commit(change)

    async def remove_model(self, provider_id: str, model_id: str) -> None:
        def change() -> tuple[Provider, ...]:
            provider = self.get(provider_id)
            models = tuple(m for m in provider.models if m.model_id != model_id)
            return self._replace_provider(replace(provider, models=models))

        await self._commit(change)

    def _replace_provider(self, updated: Provider) -> tuple[Provider, ...]:
        return tuple(updated if p.provider_id == updated.provider_id else p for p in self.providers)

    async def _commit(self, change: Callable[[], tuple[Provider, ...]]) -> None:
        await self._writer.update(
            lambda state: replace(state, providers=tuple(self._writer.clamp(p) for p in change()))
        )
        self._notify()

    def _notify(self) -> None:
        for listener in self._listeners:
            listener()
