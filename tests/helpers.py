"""Shared test utilities."""

import asyncio
import functools
from collections.abc import Callable, Coroutine


def run_async[**P](test: Callable[P, Coroutine[None, None, None]]) -> Callable[P, None]:
    """Run an async test body to completion; avoids a pytest plugin for a handful of tests."""

    @functools.wraps(test)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return wrapper
