import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends import fail
from keyring.errors import PasswordDeleteError

from kikoe.storage.secrets import KeyringSecretStore, SecretStoreError


class MemoryKeyring(KeyringBackend):
    priority = 1  # pyright: ignore[reportAssignmentType, reportIncompatibleMethodOverride]  # keyring declares a property

    def __init__(self) -> None:
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.items:
            raise PasswordDeleteError("missing")
        del self.items[(service, username)]


@pytest.fixture
def memory_keyring() -> MemoryKeyring:
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    return backend


def test_api_key_round_trips_through_the_keyring(memory_keyring: MemoryKeyring) -> None:
    store = KeyringSecretStore()
    store.set("p1", "secret")
    assert store.get("p1") == "secret"
    assert "secret" in memory_keyring.items.values()


def test_deleting_a_missing_key_is_not_an_error(memory_keyring: MemoryKeyring) -> None:
    KeyringSecretStore().delete("never-stored")
    assert memory_keyring.items == {}


def test_missing_backend_is_reported_instead_of_storing_in_plain_text() -> None:
    keyring.set_keyring(fail.Keyring())
    with pytest.raises(SecretStoreError, match="credential store"):
        KeyringSecretStore().set("p1", "secret")
