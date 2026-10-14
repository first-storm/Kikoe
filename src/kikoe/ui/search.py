"""Reject results from superseded sidebar queries."""

import asyncio
from collections.abc import Callable, Coroutine

from kikoe.core.models import NoteSummary
from kikoe.storage.notes import NoteRepository


class NoteSearch:
    """Own cancellation and request identity without owning sidebar widgets."""

    def __init__(
        self,
        notes: NoteRepository,
        start_task: Callable[[Coroutine[None, None, None]], asyncio.Task[None]],
        query: Callable[[], str],
        publish: Callable[[tuple[NoteSummary, ...]], None],
    ) -> None:
        self._notes = notes
        self._start_task = start_task
        self._query = query
        self._publish = publish
        self._generation = 0
        self._task: asyncio.Task[None] | None = None

    def refresh(self) -> None:
        """The SearchEntry supplies the debounce; each refresh supersedes prior work."""
        self.close()
        query = self._query().strip()
        if not query:
            self._publish(self._notes.notes)
            return
        self._task = self._start_task(self._search(query, self._generation))

    def close(self) -> None:
        self._generation += 1
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _search(self, query: str, generation: int) -> None:
        matches = await self._notes.search(query)
        if generation == self._generation and query == self._query().strip():
            self._publish(matches)
