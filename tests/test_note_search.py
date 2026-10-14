"""Superseded sidebar queries cannot publish after newer queries or window closure."""

import asyncio
from collections.abc import Coroutine
from pathlib import Path

import pytest

from helpers import run_async
from kikoe.core.models import NoteSummary
from kikoe.storage.library import Library
from kikoe.storage.notes import NoteRepository
from kikoe.ui.search import NoteSearch


@run_async
async def test_stale_results_are_rejected_even_when_search_ignores_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))
    await notes.load()
    await notes.create_note("First")
    first = notes.notes
    await notes.create_note("Second")
    second = notes.notes[:1]
    started: asyncio.Queue[str] = asyncio.Queue()
    releases = {"first": asyncio.Event(), "second": asyncio.Event()}

    async def find(query: str) -> tuple[NoteSummary, ...]:
        started.put_nowait(query)
        try:
            await releases[query].wait()
        except asyncio.CancelledError:
            await releases[query].wait()
        return first if query == "first" else second

    monkeypatch.setattr(notes, "search", find)
    tasks: list[asyncio.Task[None]] = []

    def start(coroutine: Coroutine[None, None, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task

    published: list[tuple[NoteSummary, ...]] = []
    query = "first"
    search = NoteSearch(notes, start, lambda: query, published.append)
    search.refresh()
    assert await started.get() == "first"
    query = "second"
    search.refresh()
    assert await started.get() == "second"
    releases["second"].set()
    await tasks[1]
    releases["first"].set()
    await tasks[0]
    assert published == [second]


@run_async
async def test_empty_query_restores_all_notes_and_close_rejects_late_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notes = NoteRepository(Library(tmp_path / "library.sqlite3", tmp_path / "audio"))
    await notes.load()
    await notes.create_note("First")
    started, release = asyncio.Event(), asyncio.Event()

    async def find(_query: str) -> tuple[NoteSummary, ...]:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return notes.notes

    monkeypatch.setattr(notes, "search", find)
    tasks: list[asyncio.Task[None]] = []

    def start(coroutine: Coroutine[None, None, None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task

    published: list[tuple[NoteSummary, ...]] = []
    query = "first"
    search = NoteSearch(notes, start, lambda: query, published.append)
    search.refresh()
    await started.wait()
    query = " "
    search.refresh()
    search.close()
    release.set()
    await tasks[0]
    assert published == [notes.notes]
