"""The real main window, driven in a separate process with a display."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.gtk

SCENARIO = Path(__file__).with_name("window_scenario.py")


def run_scenario() -> dict[str, object]:
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        pytest.skip("A display is required to open the main window")
    completed = subprocess.run(
        [sys.executable, str(SCENARIO)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "PYTHONPATH": str(SCENARIO.parent.parent / "src")},
    )
    lines = [line for line in completed.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, completed.stdout + completed.stderr
    return json.loads(lines[-1].removeprefix("RESULT "))


@pytest.fixture(scope="module")
def outcome() -> dict[str, object]:
    return run_scenario()


def test_the_window_opens_without_errors(outcome: dict[str, object]) -> None:
    assert "error" not in outcome, outcome.get("error")


def test_every_window_action_is_registered(outcome: dict[str, object]) -> None:
    assert outcome.get("missing_actions") == []


@pytest.mark.parametrize(
    "check",
    [
        "new_note_waits_for_a_provider",
        "new_note_enabled_with_a_provider",
        "new_note_opens_without_saving",
        "new_note_prefills_and_selects_title",
        "new_note_rejects_blank_titles",
        "new_note_cancel_does_not_save",
        "new_note_action_creates_and_selects",
        "new_note_saves_trimmed_custom_title",
        "new_note_prevents_duplicate_saves",
        "new_note_failure_preserves_input",
        "new_note_can_retry_after_failure",
        "lists_both_notes",
        "opens_selected_note",
        "highlights_selected_row",
        "search_narrows_the_list",
        "no_match_placeholder",
        "leaving_search_restores_the_list",
    ],
)
def test_sidebar_behavior(outcome: dict[str, object], check: str) -> None:
    assert outcome.get(check) is True
