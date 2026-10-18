"""Decide which asynchronous note reads may still be shown."""


class OpenRequests:
    """Remembers the note the user last asked for, so older reads cannot replace it.

    A read that was started by the user always wins over a reload that the app started
    for the previously open note, even if the reload finishes later.
    """

    def __init__(self) -> None:
        self._target: str | None = None

    def start(self, note_id: str, *, reload: bool) -> bool:
        """Register a read; False when it is already outdated and need not be made."""
        if not reload:
            self._target = note_id
            return True
        return self._target in (None, note_id)

    def finish(self, note_id: str, *, reload: bool, open_id: str | None) -> bool:
        """Whether the note just read may be shown; ``open_id`` is the note on screen."""
        if self._target not in (None, note_id):
            return False
        if reload:
            return open_id == note_id
        self._target = None
        return True
