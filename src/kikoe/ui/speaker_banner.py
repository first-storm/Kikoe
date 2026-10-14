"""The banner that asks to match speakers of a recording that has none assigned yet."""

from gi.repository import Adw, GLib

from kikoe.core.models import Note
from kikoe.core.speakers import unresolved_recordings
from kikoe.i18n import _


def update_speaker_banner(banner: Adw.Banner, note: Note) -> None:
    """Reveal the banner for the first recording whose speakers still need matching."""
    pending = unresolved_recordings(note)
    banner.set_action_name(None)
    banner.set_revealed(bool(pending))
    if not pending:
        return
    recording = pending[0]
    banner.set_title(
        _("Recording {number} has speakers that are not yet matched.").format(
            number=note.recordings.index(recording) + 1
        )
    )
    banner.set_action_target_value(GLib.Variant("s", recording.recording_id))
    banner.set_action_name("win.match-speakers")
