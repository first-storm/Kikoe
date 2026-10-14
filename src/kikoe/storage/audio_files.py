"""Original audio kept next to the library database."""

import logging
import os
import shutil
from pathlib import Path

from kikoe.i18n import _
from kikoe.storage.errors import LibraryError

log = logging.getLogger(__name__)


class AudioFiles:
    """Copies recordings into one directory and removes them again."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def store(self, source: Path, recording_id: str) -> Path:
        """Copy an original here so it outlives the file the user picked."""
        destination = self.directory / f"{recording_id}{source.suffix.lower()}"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # fsync on the writable handle: Windows rejects it on a read-only one (EBADF).
            with source.open("rb") as origin, destination.open("wb") as copy:
                shutil.copyfileobj(origin, copy)
                copy.flush()
                os.fsync(copy.fileno())
        except OSError as error:
            self.discard(destination)
            raise LibraryError(
                _("Could not copy the audio file into the library: {error}").format(error=error)
            ) from error
        return destination

    def discard(self, path: Path) -> None:
        """Remove a retained audio file; failure only costs disk space, so it is logged."""
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            log.warning("Could not delete %s: %s", path, error)
