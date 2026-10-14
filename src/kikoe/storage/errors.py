"""Failures of the note library that the user should read."""


class LibraryError(Exception):
    """The library cannot be read or written. The message is meant for the user."""


class NoteNotFoundError(LookupError):
    """A note was deleted while something still referred to it."""
