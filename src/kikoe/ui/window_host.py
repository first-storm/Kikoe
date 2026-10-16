"""What the main window offers the controllers that work on its behalf."""

from typing import Protocol

from kikoe.core.activity import Stage
from kikoe.core.models import Note


class WindowHost(Protocol):
    """The main window as seen by its controllers; `Window` satisfies it."""

    @property
    def note(self) -> Note | None:
        """The note shown in the content pane."""
        ...

    def show_note(self, note: Note | None) -> None: ...
    def apply_segment_edit(self, note: Note, recording_id: str, position: int) -> None:
        """Show ``note`` after an edit of one segment of the open note."""
        ...

    def select_note(self, note_id: str) -> None: ...
    def toast(self, title: str) -> None: ...
    def show_content_pane(self) -> None:
        """On narrow windows, bring the content pane in front of the sidebar."""
        ...

    def set_titles(self, page: str, title: str, subtitle: str) -> None: ...
    def set_subtitle(self, subtitle: str) -> None: ...
    def refresh_content(self) -> None: ...
    def refresh_actions(self) -> None: ...
    def set_stage(self, stage: Stage, message: str = "") -> None:
        """Enter ``stage``: the bottom bar and the enabled actions follow it.

        ``message`` replaces the bar's default text for the stage, e.g. a recording's
        model summary or a chunk count.
        """
        ...

    def set_action_enabled(self, name: str, enabled: bool) -> None: ...
