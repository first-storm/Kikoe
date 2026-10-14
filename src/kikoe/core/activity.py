"""Which actions the main window offers while work is running."""

from dataclasses import dataclass

from kikoe.core.recording import CaptureState

NOTE_CONTENT_ACTIONS = (
    "play-recording",
    "play-sample",
    "edit-speaker",
    "match-speakers",
    "retranscribe-recording",
    "delete-recording",
    "delete-note",
    "rename-note",
    "export",
    "export-audio",
)
"""Actions that read or change the shown note, so they wait for running work on it."""

SIDEBAR_ACTIONS = ("search-notes", "rename-note-row", "delete-note-row")
"""Actions on the notes list, which stay out of the way of running work."""


@dataclass(frozen=True, slots=True)
class Activity:
    """A snapshot of what the window is doing, from which every action state follows."""

    importing: bool = False
    """A file transcription (import, retry or re-transcription) is running."""
    recording: bool = False
    """A recording session owns the microphone."""
    recovering: bool = False
    """Unfinished recordings are being recovered."""
    committing: bool = False
    """The transcription result is being saved, which can no longer be cancelled."""
    closing: bool = False
    capture_state: CaptureState = CaptureState.STOPPED
    has_note: bool = False
    viewing_transcription: bool = False
    """The note on screen is the one a running transcription writes to."""
    has_model: bool = False
    """A transcription model is selected."""
    model_transcribes_files: bool = False
    has_providers: bool = False

    @property
    def busy(self) -> bool:
        """Work is running that new work must not overlap."""
        return self.importing or self.recording or self.recovering

    @property
    def switch_locked(self) -> bool:
        """The open note must stay put; transcription alone never pins it."""
        return self.recovering or (self.recording and not self.importing)

    @property
    def locked(self) -> bool:
        """The shown note must not be changed, because work is writing to it."""
        return self.switch_locked or self.viewing_transcription


def action_availability(activity: Activity) -> dict[str, bool]:
    """Whether each action that depends on running work is enabled."""
    can_use_note = not activity.locked and activity.has_note
    available = activity.has_model and not activity.busy
    can_transcribe_files = available and activity.model_transcribes_files
    can_control = not activity.closing
    state = activity.capture_state
    enabled: dict[str, bool] = dict.fromkeys(NOTE_CONTENT_ACTIONS, can_use_note)
    enabled.update(dict.fromkeys(SIDEBAR_ACTIONS, not activity.busy))
    enabled.update(
        {
            "import-audio": can_transcribe_files,
            "start-recording": available,
            "retry-recording": can_transcribe_files and activity.has_note,
            "cancel-import": activity.importing and not activity.committing,
            "pause-recording": state is CaptureState.RECORDING and can_control,
            "resume-recording": state is CaptureState.PAUSED and can_control,
            "stop-recording": state is not CaptureState.STOPPED and can_control,
            "new-note": not activity.busy and activity.has_providers,
        }
    )
    return enabled
