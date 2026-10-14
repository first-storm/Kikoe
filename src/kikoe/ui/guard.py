"""A reentrant flag for code that fills widgets, so their change handlers can tell."""


class Guard:
    """Active inside ``with guard:`` blocks, which may nest; ``depth`` > 0 starts it held."""

    __slots__ = ("_depth",)

    def __init__(self, depth: int = 0) -> None:
        self._depth = depth

    @property
    def active(self) -> bool:
        return self._depth > 0

    def __enter__(self) -> None:
        self._depth += 1

    def __exit__(self, *_exception: object) -> None:
        self._depth -= 1
