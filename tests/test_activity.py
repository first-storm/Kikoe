from dataclasses import replace

import pytest

from kikoe.core.activity import (
    NOTE_CONTENT_ACTIONS,
    SIDEBAR_ACTIONS,
    Activity,
    Stage,
    action_availability,
)
from kikoe.core.recording import CaptureState

READY = Activity(has_note=True, has_model=True, model_transcribes_files=True, has_providers=True)


def enabled(activity: Activity) -> set[str]:
    return {name for name, on in action_availability(activity).items() if on}


def test_an_idle_window_offers_everything_except_running_work_controls() -> None:
    actions = enabled(READY)
    assert actions == {
        *NOTE_CONTENT_ACTIONS,
        *SIDEBAR_ACTIONS,
        "import-audio",
        "start-recording",
        "retry-recording",
        "new-note",
    }


def test_without_providers_or_a_model_nothing_can_be_created_or_transcribed() -> None:
    actions = enabled(Activity(has_note=True))
    assert actions.isdisjoint({"import-audio", "start-recording", "retry-recording", "new-note"})
    assert set(NOTE_CONTENT_ACTIONS) <= actions


def test_a_model_without_file_support_can_record_but_not_import_or_retry() -> None:
    actions = enabled(
        Activity(has_note=True, has_model=True, model_transcribes_files=False, has_providers=True)
    )
    assert "start-recording" in actions
    assert actions.isdisjoint({"import-audio", "retry-recording"})


def test_retry_needs_a_shown_note() -> None:
    assert "retry-recording" not in enabled(
        Activity(has_model=True, model_transcribes_files=True, has_providers=True)
    )


def test_recording_pins_the_note_and_blocks_new_work() -> None:
    activity = Activity(
        stage=Stage.RECORDING,
        recording=True,
        capture_state=CaptureState.RECORDING,
        has_note=True,
        has_model=True,
        model_transcribes_files=True,
        has_providers=True,
    )
    assert activity.switch_locked
    actions = enabled(activity)
    assert actions == {"pause-recording", "stop-recording"}


def test_pausing_swaps_pause_for_resume() -> None:
    activity = Activity(stage=Stage.RECORDING, recording=True, capture_state=CaptureState.PAUSED)
    assert enabled(activity) == {"resume-recording", "stop-recording"}


def test_closing_disables_the_recording_controls() -> None:
    activity = Activity(
        stage=Stage.RECORDING, recording=True, capture_state=CaptureState.RECORDING, closing=True
    )
    assert enabled(activity) == set()


def test_transcription_alone_does_not_pin_the_open_note() -> None:
    activity = replace(READY, importing=True, stage=Stage.TRANSCRIBING)
    assert not activity.switch_locked
    assert not activity.locked
    actions = enabled(activity)
    assert set(NOTE_CONTENT_ACTIONS) - {"retranscribe-recording"} <= actions
    assert "cancel-import" in actions
    assert actions.isdisjoint(set(SIDEBAR_ACTIONS) | {"import-audio", "new-note"})


def test_the_note_being_transcribed_is_locked() -> None:
    activity = Activity(
        stage=Stage.TRANSCRIBING, importing=True, has_note=True, viewing_transcription=True
    )
    assert activity.locked
    assert enabled(activity) == {"cancel-import"}


def test_saving_a_transcription_cannot_be_cancelled() -> None:
    assert enabled(Activity(stage=Stage.SAVING, importing=True)) == set()


def test_a_recording_during_a_retry_does_not_pin_the_note() -> None:
    activity = Activity(recording=True, importing=True)
    assert not activity.switch_locked


def test_recovery_pins_the_note_and_blocks_new_work() -> None:
    activity = Activity(stage=Stage.RECOVERING, has_note=True, has_model=True, has_providers=True)
    assert activity.switch_locked
    assert enabled(activity) == set()


@pytest.mark.parametrize("state", list(CaptureState))
def test_stop_is_offered_whenever_capture_is_not_stopped(state: CaptureState) -> None:
    activity = Activity(stage=Stage.RECORDING, recording=True, capture_state=state)
    assert ("stop-recording" in enabled(activity)) is (state is not CaptureState.STOPPED)


def test_cancelling_a_transcription_disables_repeated_cancel() -> None:
    assert enabled(Activity(stage=Stage.CANCELLING, importing=True)) == set()


@pytest.mark.parametrize("stage", [Stage.STOPPING, Stage.FINALIZING, Stage.SAVING_AUDIO])
def test_finishing_a_capture_disables_pause_resume_and_stop(stage: Stage) -> None:
    activity = Activity(stage=stage, recording=True, capture_state=CaptureState.RECORDING)
    assert enabled(activity) == set()


def test_retranscribing_waits_while_another_transcription_runs() -> None:
    assert "retranscribe-recording" in enabled(READY)
    assert "retranscribe-recording" not in enabled(
        replace(READY, importing=True, stage=Stage.TRANSCRIBING)
    )


@pytest.mark.parametrize("stage", list(Stage))
def test_only_file_stages_show_cancel_and_only_working_ones_enable_it(stage: Stage) -> None:
    file_stages = {Stage.CONVERTING, Stage.TRANSCRIBING, Stage.SAVING, Stage.CANCELLING}
    assert stage.shows_cancel is (stage in file_stages)
    assert stage.can_cancel is (stage in {Stage.CONVERTING, Stage.TRANSCRIBING})
    activity = Activity(stage=stage, importing=True)
    assert ("cancel-import" in enabled(activity)) is stage.can_cancel
