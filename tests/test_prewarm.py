"""Prewarming loads the provider's client code and never fails the app."""

import logging

import pytest

from helpers import run_async
from kikoe.core.models import ApiKind, Provider
from kikoe.providers.base import TranscriptionProvider
from kikoe.services import prewarm

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
