"""Which failures carry a message written for the user."""

from kikoe.media.ffmpeg import FFmpegError
from kikoe.media.recorder import RecorderError
from kikoe.providers.base import TranscriptionError
from kikoe.storage.captures import CaptureStoreError
from kikoe.storage.downloads import DownloadError
from kikoe.storage.errors import LibraryError, NoteNotFoundError
from kikoe.storage.local_installs import InstallError
from kikoe.storage.providers import ProviderNotFoundError
from kikoe.storage.secrets import SecretStoreError
from kikoe.storage.settings import SettingsError

USER_FACING_ERRORS = (
    SettingsError,
    SecretStoreError,
    ProviderNotFoundError,
    LibraryError,
    NoteNotFoundError,
    TranscriptionError,
    FFmpegError,
    RecorderError,
    CaptureStoreError,
    DownloadError,
    InstallError,
)
"""Exceptions whose message is written for the user and can be shown as is."""
