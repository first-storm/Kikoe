"""Decide whether files dropped on the window can be imported as audio."""

import mimetypes
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path


class DropRejection(StrEnum):
    """Why a drop cannot be imported."""

    SEVERAL = "several"
    NOT_LOCAL = "not-local"
    NOT_MEDIA = "not-media"


def dropped_audio(paths: Sequence[Path | None]) -> Path | DropRejection:
    """The one file to import from a drop; a path of None is a file that is not local.

    Only one import runs at a time, so several files are refused instead of guessing
    which one was meant. An unknown extension is let through: ffmpeg knows more
    formats than the extension table does, and explains itself when it cannot read one.
    """
    if len(paths) != 1:
        return DropRejection.SEVERAL
    path = paths[0]
    if path is None:
        return DropRejection.NOT_LOCAL
    kind, _encoding = mimetypes.guess_type(path.name)
    if kind is not None and not kind.startswith(("audio/", "video/")):
        return DropRejection.NOT_MEDIA
    return path
