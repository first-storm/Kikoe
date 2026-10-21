"""Decide which asynchronous note reads may still be shown."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenRequest:
    """A read belongs to the user selection that was current when it started."""

    note_id: str
    reload: bool
    generation: int


class OpenRequests:
    """Remembers the note the user last asked for, so older reads cannot replace it.

    A read that was started by the user always wins over a reload that the app started
    for the previously open note, even if the reload finishes later.
    """

    def __init__(self) -> None:
        self._target: str | None = None
        self._generation = 0

    def start(self, note_id: str, *, reload: bool) -> OpenRequest | None:
        """Register a read; None when it is already outdated and need not be made."""
        if reload and self._target not in (None, note_id):
            return None
        if not reload:
            self._target = note_id
            self._generation += 1
        return OpenRequest(note_id, reload, self._generation)

    def finish(self, request: OpenRequest, *, open_id: str | None) -> bool:
        """Whether the note just read may be shown; ``open_id`` is the note on screen."""
        if request.generation != self._generation:
            return False
        if request.reload:
            return open_id == request.note_id
        self._target = None
        return True

    def cancel(self, request: OpenRequest) -> None:
        """Release a failed read without disturbing a more recent user selection."""
        if not request.reload and request.generation == self._generation:
            self._target = None
