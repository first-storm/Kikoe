"""Keep temporary file transcripts attached to their target note."""

from gi.repository import GLib

from kikoe.core.models import Note, Recording
from kikoe.core.transcription import FileTranscriptSnapshot
from kikoe.ui.note_view import NoteView


class FileTranscriptionDisplay:
    """Own preview scheduling and visibility while leaving saved notes untouched."""

    def __init__(self, view: NoteView) -> None:
        self._view = view
        self._recording: Recording | None = None
        self.note_id: str | None = None
        self._replacing = False
        self._shown = False
        self._timeout = 0
        self._pending: FileTranscriptSnapshot | None = None
        self._rendered = FileTranscriptSnapshot()

    @property
    def active(self) -> bool:
        return self._recording is not None

    def begin(self, note: Note | None, recording: Recording, *, replacing: bool) -> None:
        self.end()
        self._recording = recording
        self.note_id = note.note_id if note else None
        self._replacing = replacing
        self._show(note)

    def hide_if_other(self, note: Note | None) -> None:
        if self._shown and (note is None or note.note_id != self.note_id):
            self._view.end_file_transcription()
            self._shown = False

    def show(self, note: Note) -> None:
        """Restore the latest preview when returning to the job's target note."""
        if self.active and note.note_id == self.note_id and not self._shown:
            self._show(note)
            self._view.update_file_transcription(self._rendered)

    def _show(self, note: Note | None) -> None:
        recording = self._recording
        if recording is None:
            return
        self._view.begin_file_transcription(
            note,
            recording,
            replace_recording_id=recording.recording_id if self._replacing else None,
        )
        self._shown = True

    def update(self, snapshot: FileTranscriptSnapshot) -> None:
        if self._recording is None:
            return
        self._pending = snapshot.scoped(self._recording.recording_id)
        if self._pending.segments != self._rendered.segments or not snapshot.preview:
            self._cancel_timeout()
            self._render()
        elif not self._timeout:
            self._timeout = GLib.timeout_add(50, self._render)

    def _render(self) -> bool:
        self._timeout = 0
        if self.active and self._pending is not None:
            self._rendered = self._pending
            self._pending = None
            if self._shown:
                self._view.update_file_transcription(self._rendered)
        return GLib.SOURCE_REMOVE

    def end(self) -> None:
        """Remove scheduled updates and temporary rows when a job ends."""
        self._cancel_timeout()
        self._pending = None
        self._rendered = FileTranscriptSnapshot()
        self._recording = None
        self.note_id = None
        if self._shown:
            self._view.end_file_transcription()
            self._shown = False

    def _cancel_timeout(self) -> None:
        if self._timeout:
            GLib.source_remove(self._timeout)
            self._timeout = 0
