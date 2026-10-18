from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

import pytest

from kikoe.core.export import ExportFormat, export_filename, export_note
from kikoe.core.models import (
    DisplayOptions,
    Note,
    Recording,
    RecordingSource,
    Segment,
    Speaker,
)

_SHOW_ALL = DisplayOptions(show_timestamps=True, show_speakers=True)
_HIDE_ALL = DisplayOptions(show_timestamps=False, show_speakers=False)
_CREATED = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)


def _segment(
    text: str, *, start: float | None = None, end: float | None = None, speaker: str | None = None
) -> Segment:
    return Segment(
        text,
        start=timedelta(seconds=start) if start is not None else None,
        end=timedelta(seconds=end) if end is not None else None,
        speaker_id=speaker,
    )


def _recording(
    segments: Iterable[Segment] = (),
    *,
    recording_id: str = "r1",
    duration: timedelta = timedelta(seconds=60),
) -> Recording:
    return Recording(
        recording_id=recording_id,
        created_at=_CREATED,
        duration=duration,
        source=RecordingSource.IMPORTED,
        provider_name="Deepgram",
        model_id="nova-3",
        segments=tuple(segments),
    )


def _note(
    recordings: Iterable[Recording] = (),
    *,
    title: str = "Podcast Topics",
    speakers: Iterable[Speaker] = (),
) -> Note:
    return Note(
        note_id="n1",
        title=title,
        created_at=_CREATED,
        recordings=tuple(recordings),
        speakers=tuple(speakers),
    )


def _named_note() -> Note:
    segments = [
        _segment("Hello there", start=9, speaker="k"),
        _segment("Hi", start=65, speaker="j"),
    ]
    speakers = [Speaker("k", "Kobayashi"), Speaker("j", "Joe")]
    return _note([_recording(segments)], speakers=speakers)


def test_text_export_shows_timestamps_and_speakers() -> None:
    assert export_note(_named_note(), ExportFormat.TEXT, _SHOW_ALL) == (
        "Podcast Topics\n\n[00:09] Kobayashi: Hello there\n[01:05] Joe: Hi\n"
    )


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (
            _SHOW_ALL,
            "Podcast Topics\n\n[00:09] Kobayashi: Hello there\n[01:05] Joe: Hi\n",
        ),
        (
            DisplayOptions(True, False),
            "Podcast Topics\n\n[00:09] Hello there\n[01:05] Hi\n",
        ),
        (
            DisplayOptions(False, True),
            "Podcast Topics\n\nKobayashi: Hello there\nJoe: Hi\n",
        ),
        (_HIDE_ALL, "Podcast Topics\n\nHello there\nHi\n"),
    ],
)
def test_text_export_honors_display_options(options: DisplayOptions, expected: str) -> None:
    assert export_note(_named_note(), ExportFormat.TEXT, options) == expected


def test_markdown_bolds_speaker_names_and_headers() -> None:
    assert export_note(_named_note(), ExportFormat.MARKDOWN, _SHOW_ALL) == (
        "# Podcast Topics\n\n[00:09] **Kobayashi:** Hello there\n[01:05] **Joe:** Hi\n"
    )


@pytest.mark.parametrize("fmt", [ExportFormat.TEXT, ExportFormat.MARKDOWN])
def test_single_recording_omits_recording_headers(fmt: ExportFormat) -> None:
    assert "Recording" not in export_note(_named_note(), fmt, _SHOW_ALL)


def test_multi_recording_numbers_sections() -> None:
    note = _note([_recording([_segment("One", start=1)]), _recording([_segment("Two", start=2)])])

    assert export_note(note, ExportFormat.TEXT, _SHOW_ALL) == (
        "Podcast Topics\n\nRecording 1\n\n[00:01] One\n\nRecording 2\n\n[00:02] Two\n"
    )


def test_custom_speaker_name_preferred_over_fallback() -> None:
    segments = [_segment("Hi", speaker="a"), _segment("Yo", speaker="b")]
    note = _note([_recording(segments)], speakers=[Speaker("a", "Ada")])
    options = DisplayOptions(show_timestamps=False, show_speakers=True)

    assert (
        export_note(note, ExportFormat.TEXT, options)
        == "Podcast Topics\n\nAda: Hi\nSpeaker 2: Yo\n"
    )


def test_fallback_speaker_numbering_is_note_wide() -> None:
    note = _note(
        [
            _recording([_segment("First", speaker="x")]),
            _recording([_segment("Second", speaker="y")], recording_id="r2"),
        ]
    )
    options = DisplayOptions(show_timestamps=False, show_speakers=True)

    assert export_note(note, ExportFormat.TEXT, options) == (
        "Podcast Topics\n\nRecording 1\n\nSpeaker 1: First\n\nRecording 2\n\nSpeaker 2: Second\n"
    )


def test_prose_export_uses_screen_timestamp_format() -> None:
    note = _note([_recording([_segment("Hi", start=9.99)])])

    assert export_note(note, ExportFormat.TEXT, _SHOW_ALL) == "Podcast Topics\n\n[00:09] Hi\n"


def test_prose_export_shows_timestamp_ranges() -> None:
    segments = [
        _segment("Hello", start=9, end=14),
        _segment("Starting", start=65),
        _segment("Ending", end=70),
        _segment("Untimed"),
    ]
    note = _note([_recording(segments)])

    assert export_note(note, ExportFormat.TEXT, _SHOW_ALL) == (
        "Podcast Topics\n\n[00:09–00:14] Hello\n[01:05] Starting\n[01:10] Ending\nUntimed\n"  # noqa: RUF001 -- asserts the en-dash range separator shown in exports
    )


def test_srt_numbering_is_continuous_across_recordings() -> None:
    note = _note(
        [
            _recording([_segment("One", start=1, end=3)]),
            _recording([_segment("Two", start=1, end=4)], recording_id="r2"),
        ]
    )

    assert export_note(note, ExportFormat.SRT, _HIDE_ALL) == (
        "1\n00:00:01,000 --> 00:00:03,000\nOne\n\n2\n00:01:01,000 --> 00:01:04,000\nTwo\n"
    )


def test_srt_uses_comma_and_vtt_uses_dot_with_header() -> None:
    note = _note([_recording([_segment("One", start=1, end=3)])])

    assert export_note(note, ExportFormat.SRT, _HIDE_ALL) == (
        "1\n00:00:01,000 --> 00:00:03,000\nOne\n"
    )
    assert export_note(note, ExportFormat.WEBVTT, _HIDE_ALL) == (
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nOne\n"
    )


def test_milliseconds_round_up_with_carry() -> None:
    note = _note([_recording([_segment("Hi", start=59.9999, end=61.2)])])

    assert export_note(note, ExportFormat.SRT, _HIDE_ALL) == (
        "1\n00:01:00,000 --> 00:01:01,200\nHi\n"
    )


def test_missing_end_defaults_to_next_start() -> None:
    segments = [_segment("One", start=1), _segment("Two", start=5, end=7)]
    note = _note([_recording(segments)])

    assert export_note(note, ExportFormat.SRT, _HIDE_ALL) == (
        "1\n00:00:01,000 --> 00:00:05,000\nOne\n\n2\n00:00:05,000 --> 00:00:07,000\nTwo\n"
    )


def test_missing_end_on_last_segment_defaults_to_two_seconds() -> None:
    note = _note([_recording([_segment("Hi", start=1)])])

    assert (
        export_note(note, ExportFormat.SRT, _HIDE_ALL) == "1\n00:00:01,000 --> 00:00:03,000\nHi\n"
    )


def test_end_before_start_is_clamped_to_one_second() -> None:
    note = _note([_recording([_segment("Hi", start=5, end=2)])])

    assert (
        export_note(note, ExportFormat.SRT, _HIDE_ALL) == "1\n00:00:05,000 --> 00:00:06,000\nHi\n"
    )


def test_missing_timestamps_get_sequential_slots() -> None:
    note = _note([_recording([_segment("One"), _segment("Two")])])

    assert export_note(note, ExportFormat.SRT, _HIDE_ALL) == (
        "1\n00:00:00,000 --> 00:00:02,000\nOne\n\n2\n00:00:02,000 --> 00:00:04,000\nTwo\n"
    )


def test_second_recording_offsets_by_first_duration() -> None:
    note = _note(
        [
            _recording([_segment("One", start=1, end=3)], duration=timedelta(seconds=90)),
            _recording([_segment("Two", start=1, end=2)], recording_id="r2"),
        ]
    )

    assert export_note(note, ExportFormat.SRT, _HIDE_ALL) == (
        "1\n00:00:01,000 --> 00:00:03,000\nOne\n\n2\n00:01:31,000 --> 00:01:32,000\nTwo\n"
    )


def test_subtitle_export_always_includes_timestamps() -> None:
    note = _note([_recording([_segment("One", start=1, end=3)])])

    assert export_note(note, ExportFormat.SRT, _HIDE_ALL) == (
        "1\n00:00:01,000 --> 00:00:03,000\nOne\n"
    )


def test_srt_prefixes_speaker_with_colon() -> None:
    segments = [_segment("Hello", start=1, end=3, speaker="k")]
    note = _note([_recording(segments)], speakers=[Speaker("k", "Kobayashi")])

    assert export_note(note, ExportFormat.SRT, _SHOW_ALL) == (
        "1\n00:00:01,000 --> 00:00:03,000\nKobayashi: Hello\n"
    )


def test_vtt_uses_voice_tags_and_escapes_markup() -> None:
    segments = [_segment("a < b & c", start=1, end=3, speaker="k")]
    note = _note([_recording(segments)], speakers=[Speaker("k", "A>B")])

    assert export_note(note, ExportFormat.WEBVTT, _SHOW_ALL) == (
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v A&gt;B>a &lt; b &amp; c</v>\n"
    )


@pytest.mark.parametrize("fmt", [ExportFormat.SRT, ExportFormat.WEBVTT])
def test_speaker_prefix_hidden_when_show_speakers_off(fmt: ExportFormat) -> None:
    segments = [_segment("Hello", start=1, end=3, speaker="k")]
    note = _note([_recording(segments)], speakers=[Speaker("k", "Kobayashi")])

    assert "Kobayashi" not in export_note(note, fmt, DisplayOptions(True, False))


def test_empty_note_exports_title_only_prose() -> None:
    note = _note()

    assert export_note(note, ExportFormat.TEXT, _SHOW_ALL) == "Podcast Topics\n"
    assert export_note(note, ExportFormat.MARKDOWN, _SHOW_ALL) == "# Podcast Topics\n"


def test_empty_note_exports_empty_srt_and_bare_vtt_header() -> None:
    note = _note()

    assert export_note(note, ExportFormat.SRT, _SHOW_ALL) == ""
    assert export_note(note, ExportFormat.WEBVTT, _SHOW_ALL) == "WEBVTT\n"


def test_blank_segments_are_skipped_everywhere() -> None:
    note = _note([_recording([_segment(""), _segment("  "), _segment("Real")])])

    assert export_note(note, ExportFormat.TEXT, _SHOW_ALL) == "Podcast Topics\n\nReal\n"
    assert export_note(note, ExportFormat.MARKDOWN, _SHOW_ALL) == "# Podcast Topics\n\nReal\n"
    assert export_note(note, ExportFormat.SRT, _SHOW_ALL) == (
        "1\n00:00:00,000 --> 00:00:02,000\nReal\n"
    )
    assert export_note(note, ExportFormat.WEBVTT, _SHOW_ALL) == (
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nReal\n"
    )


@pytest.mark.parametrize(
    "fmt", [ExportFormat.TEXT, ExportFormat.MARKDOWN, ExportFormat.SRT, ExportFormat.WEBVTT]
)
def test_output_ends_with_single_newline(fmt: ExportFormat) -> None:
    note = _note([_recording([_segment("Hi", start=1, end=2)])])

    output = export_note(note, fmt, _SHOW_ALL)

    assert output.endswith("\n") and not output.endswith("\n\n")


@pytest.mark.parametrize(
    ("stem", "extension", "expected"),
    [
        ("Meeting Notes", "txt", "Meeting Notes.txt"),
        ("Q3/Q4: plans?", "md", "Q3-Q4- plans-.md"),
        ("a\tb\nc", "vtt", "a b c.vtt"),
        ('a"b|c', "txt", "a-b-c.txt"),
        ("a\x01b", "txt", "a-b.txt"),
        ("  spaced   out  ", "txt", "spaced out.txt"),
        ("", "txt", "Untitled Note.txt"),
        ("...", "srt", "Untitled Note.srt"),
    ],
)
def test_export_filename_sanitizes_stems(stem: str, extension: str, expected: str) -> None:
    assert export_filename(stem, extension) == expected


@pytest.mark.parametrize("fmt", list(ExportFormat))
def test_export_resolves_matched_speakers_consistently(fmt: ExportFormat) -> None:
    from datetime import UTC, datetime

    from kikoe.core.models import Note, Recording, RecordingSource, Speaker, SpeakerBinding

    recording = Recording(
        "r",
        datetime.now(UTC),
        timedelta(seconds=5),
        RecordingSource.IMPORTED,
        "P",
        "M",
        (Segment("Hello", timedelta(), timedelta(seconds=2), "local"),),
        speaker_bindings=(SpeakerBinding("local", "alice", "new", True),),
    )
    note = Note("n", "Meeting", datetime.now(UTC), (recording,), (Speaker("alice", "Alice", 1),))
    output = export_note(note, fmt, DisplayOptions())
    assert "Alice" in output
    assert "Speaker 1" not in output
    assert "Alice" not in export_note(note, fmt, DisplayOptions(show_speakers=False))


@pytest.mark.parametrize("fmt", [ExportFormat.SRT, ExportFormat.WEBVTT])
def test_subtitle_export_never_puts_a_blank_line_inside_a_cue(fmt: ExportFormat) -> None:
    note = _note([_recording([_segment("One\n\n  \r\nTwo", start=1, end=3)])])
    output = export_note(note, fmt, _SHOW_ALL)
    assert "One\nTwo\n" in output
    assert [block for block in output.strip().split("\n\n") if "Two" in block]
    assert len(output.strip().split("\n\n")) == (2 if fmt is ExportFormat.WEBVTT else 1)
