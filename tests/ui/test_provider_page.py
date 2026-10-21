"""Provider creation admits one save at a time and allows retry after a failure."""

from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from kikoe.storage.providers import ProviderRepository
from tests.helpers import open_settings, run_async
from tests.storage.test_providers_repository import FakeSecrets

if TYPE_CHECKING:
    from gi.repository import Gtk

    from kikoe.ui.note_view import NoteView

pytestmark = pytest.mark.gtk


@run_async
async def test_repeated_add_creates_one_provider_and_failure_can_be_retried(
    view: NoteView, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kikoe.ui import provider_page

    repository = ProviderRepository(open_settings(tmp_path / "settings.json"), FakeSecrets())
    page = provider_page.ProviderPage(repository)
    page.name_row.set_text("Test Provider")
    saves: list[Coroutine[None, None, None]] = []

    def queue(coroutine: Coroutine[None, None, None], _parent: Gtk.Widget) -> None:
        saves.append(coroutine)

    monkeypatch.setattr(provider_page, "run_task", queue)
    page.add_button.emit("clicked")
    page.add_button.emit("clicked")
    queued = len(saves)
    disabled = not page.add_button.get_sensitive()

    # A failed storage read must leave the same form available for retry.
    def fail() -> None:
        raise OSError("Simulated disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(repository._writer._store, "load_state", fail)
        for save in saves:
            with pytest.raises(OSError, match="Simulated"):
                await save
    assert queued == 1
    assert disabled
    assert page.add_button.get_sensitive()
    assert page.name_row.get_text() == "Test Provider"
    saves.clear()
    page.add_button.emit("clicked")
    await saves[0]
    assert len(repository.configured) == 1
