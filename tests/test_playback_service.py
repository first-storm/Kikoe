"""Replacement playback waits for device cleanup and ignores stale completions."""

import asyncio
from collections.abc import Coroutine
from datetime import timedelta
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.media.player import PlaybackError
from kikoe.services.playback import Playback


def start_task(coroutine: Coroutine[None, None, None]) -> asyncio.Task[None]:
    return asyncio.create_task(coroutine)


@run_async
async def test_replacement_waits_for_previous_device_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    cleanup = asyncio.Event()
    release = asyncio.Event()
    calls: list[str | None] = []

    async def play(
        _path: Path, *, start: timedelta, end: timedelta | None, device: str | None
    ) -> None:
        calls.append(device)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()
            await release.wait()

    monkeypatch.setattr("kikoe.services.playback.play_audio", play)
    highlights: list[str | None] = []
    playback = Playback(start_task, highlights.append, lambda _error: pytest.fail("unexpected"))
    playback.play(Path("audio.flac"), "first", 0, None, "first device")
    await entered.wait()
    playback.play(Path("audio.flac"), "second", 0, None, "second device")
    await cleanup.wait()
    assert calls == ["first device"]
    assert highlights[-1] == "second"
    entered.clear()
    release.set()
    await entered.wait()
    assert calls == ["first device", "second device"]
    await playback.finish()
    assert not playback.pending
    assert highlights[-1] is None


@run_async
async def test_same_range_toggles_playback_off(monkeypatch: pytest.MonkeyPatch) -> None:
    entered = asyncio.Event()

    async def play(
        _path: Path, *, start: timedelta, end: timedelta | None, device: str | None
    ) -> None:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("kikoe.services.playback.play_audio", play)
    highlights: list[str | None] = []
    playback = Playback(start_task, highlights.append, lambda _error: None)
    playback.play(Path("audio.flac"), "first", 0, None, None)
    await entered.wait()
    playback.play(Path("audio.flac"), "first", 0, None, None)
    await playback.finish()
    assert highlights[-1] is None
    assert not playback.pending


@run_async
async def test_sample_uses_requested_range_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[timedelta, timedelta | None, str | None]] = []
    errors: list[PlaybackError] = []
    finished = asyncio.Event()

    async def play(
        _path: Path, *, start: timedelta, end: timedelta | None, device: str | None
    ) -> None:
        calls.append((start, end, device))
        raise PlaybackError("device lost")

    def changed(_recording: str | None) -> None:
        if errors:
            finished.set()

    monkeypatch.setattr("kikoe.services.playback.play_audio", play)
    playback = Playback(start_task, changed, errors.append)
    playback.play(Path("audio.flac"), "first", 1.25, 2.5, "speaker")
    await finished.wait()
    assert calls == [(timedelta(seconds=1.25), timedelta(seconds=2.5), "speaker")]
    assert str(errors[0]) == "device lost"
    assert not playback.pending
