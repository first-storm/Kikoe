"""API key storage in the OS credential store, one entry per provider."""

import logging
from typing import Protocol

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from kikoe.constants import APP_ID
from kikoe.i18n import _

log = logging.getLogger(__name__)


class SecretStoreError(Exception):
    """The credential store is missing or refused the request. Message is for the user."""


class SecretStore(Protocol):
    def get(self, provider_id: str) -> str | None: ...
    def set(self, provider_id: str, api_key: str) -> None: ...
    def delete(self, provider_id: str) -> None: ...


class KeyringSecretStore:
    """Keyring-backed store. Blocking; call it via ``asyncio.to_thread``.

    Keys are never written anywhere else: if no backend is available we fail loudly
    rather than fall back to a plain file.
    """

    def get(self, provider_id: str) -> str | None:
        try:
            return keyring.get_password(APP_ID, provider_id)
        except KeyringError as error:
            raise _unavailable(error) from error

    def set(self, provider_id: str, api_key: str) -> None:
        try:
            keyring.set_password(APP_ID, provider_id, api_key)
        except KeyringError as error:
            raise _unavailable(error) from error
        log.info("Stored API key for provider %s", provider_id)

    def delete(self, provider_id: str) -> None:
        try:
            keyring.delete_password(APP_ID, provider_id)
        except PasswordDeleteError:
            log.debug("No stored API key for provider %s", provider_id)
        except KeyringError as error:
            raise _unavailable(error) from error


def _unavailable(error: KeyringError) -> SecretStoreError:
    return SecretStoreError(
        _(
            "API keys could not be saved because no secure credential store is available. "
            "Details: {error}"
        ).format(error=error)
    )
