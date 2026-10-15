"""The repository reloads summaries only after changes that can alter them."""

from datetime import UTC, datetime, timedelta

from helpers import run_async
from kikoe.core.models import (
    DisplayOptions,
    Note,
    Recording,
    RecordingSource,
    Segment,
    TranscriptionState,
)
from kikoe.storage.notes import ContentChanged, NoteEvent, NoteRepository, SummariesChanged


def recording(recording_id: str = "recording") -> Recording:
    return Recording(
        recording_id,
        datetime.now(UTC),
        timedelta(seconds=5),
        RecordingSource.IMPORTED,
        "Cloud",
        "Model",
        (Segment("Hello", speaker_id="A"),),
    )


async def note_with_events(notes: NoteRepository) -> tuple[Note, list[NoteEvent]]:
    await notes.load()
    note = await notes.add_recording(None, "Meeting", recording())
    events: list[NoteEvent] = []
    notes.connect_changed(events.append)
    return note, events


@run_async
async def test_loading_and_creating_reload_summaries(notes: NoteRepository) -> None:
    events: list[NoteEvent] = []
    notes.connect_changed(events.append)
    await notes.load()
    note = await notes.create_note("Draft")
    assert [s.title for s in notes.notes] == ["Draft"]
    assert events == [SummariesChanged(None), SummariesChanged(note.note_id)]


@run_async
async def test_renaming_a_note_reloads_its_summary(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    await notes.rename_note(note.note_id, " Standup ")
    assert [s.title for s in notes.notes] == ["Standup"]
    assert events == [SummariesChanged(note.note_id)]


@run_async
async def test_adding_and_deleting_recordings_reload_summaries(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    await notes.add_recording(note.note_id, "", recording("second"))
    assert notes.notes[0].recording_count == 2
    await notes.delete_recording(note.note_id, "second")
    assert notes.notes[0].recording_count == 1
    assert events == [SummariesChanged(note.note_id), SummariesChanged(note.note_id)]


@run_async
async def test_content_changes_keep_summaries_and_name_the_note(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    summaries = notes.notes
    speaker = note.speakers[0].speaker_id
    await notes.set_display_options(note.note_id, DisplayOptions(show_timestamps=False))
    await notes.rename_speaker(note.note_id, speaker, "Ada")
    await notes.match_speakers(note.note_id, "recording", {})
    await notes.update_transcription(
        note.note_id, "recording", TranscriptionState.READY, segments=(Segment("Again"),)
    )
    assert notes.notes is summaries
    assert events == [ContentChanged(note.note_id)] * 4
    saved = await notes.get_note(note.note_id)
    assert saved.display == DisplayOptions(show_timestamps=False)
    assert saved.recordings[0].segments == (Segment("Again"),)


@run_async
async def test_segment_edits_are_saved_without_notifying(notes: NoteRepository) -> None:
    note, events = await note_with_events(notes)
    await notes.update_segment(
        note.note_id, "recording", 0, text="Edited", start_ms=None, end_ms=None
    )
    assert events == []
    saved = await notes.get_note(note.note_id)
    assert saved.recordings[0].segments[0].text == "Edited"
