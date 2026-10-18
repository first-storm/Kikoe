"""Prewarming prepares SDKs and credentials without failing the app."""

import asyncio
import logging

import pytest

from kikoe.core.models import ApiKind, Provider
from kikoe.providers.base import TranscriptionProvider
from kikoe.services import prewarm
from kikoe.storage.secrets import SecretStoreError
from tests.helpers import run_async

PROVIDER = Provider("p1", "Test", ApiKind.GROQ, "https://example.test/v1")


@run_async
async def test_prewarm_builds_a_client_for_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[tuple[Provider, str]] = []

    def create(provider: Provider, key: str) -> TranscriptionProvider:
        built.append((provider, key))
        return object()  # pyright: ignore[reportReturnType]  # only the call matters here

    monkeypatch.setattr(prewarm, "create_client", create)
    await prewarm.prewarm_provider(PROVIDER)
    assert built == [(PROVIDER, "")]


@run_async
async def test_prewarm_failure_is_logged_not_raised(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def create(provider: Provider, key: str) -> TranscriptionProvider:
        raise ImportError("broken SDK")

    monkeypatch.setattr(prewarm, "create_client", create)
    with caplog.at_level(logging.WARNING):
        await prewarm.prewarm_provider(PROVIDER)
    assert "p1" in caplog.text


@run_async
async def test_prewarm_reads_the_selected_providers_key_without_logging_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requested: list[str] = []

    class Keys:
        async def get_api_key(self, provider_id: str) -> str:
            requested.append(provider_id)
            return "private-api-key"

    with caplog.at_level(logging.DEBUG):
        await prewarm.prewarm_api_key(PROVIDER, Keys())

    assert requested == [PROVIDER.provider_id]
    assert "private-api-key" not in caplog.text


@run_async
async def test_key_prewarm_failure_is_logged_without_error_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Keys:
        async def get_api_key(self, provider_id: str) -> str:
            raise SecretStoreError("private-error-details")

    with caplog.at_level(logging.DEBUG):
        await prewarm.prewarm_api_key(PROVIDER, Keys())

    assert PROVIDER.provider_id in caplog.text
    assert "private-error-details" not in caplog.text


@run_async
async def test_waiting_for_key_prewarm_keeps_the_event_loop_responsive() -> None:
    reading = asyncio.Event()
    release = asyncio.Event()

    class Keys:
        async def get_api_key(self, provider_id: str) -> str:
            reading.set()
            await release.wait()
            return "key"

    task = asyncio.create_task(prewarm.prewarm_api_key(PROVIDER, Keys()))
    try:
        async with asyncio.timeout(1):
            await reading.wait()
            assert not task.done()
            release.set()
            await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
