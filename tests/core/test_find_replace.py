from datetime import UTC, datetime, timedelta

import pytest

from kikoe.core.find_replace import (
    FindQuery,
    InvalidPatternError,
    InvalidReplacementError,
    TextMatch,
    compile_query,
    find_matches,
    first_match_from,
    replace_matches,
)
from kikoe.core.models import Note, Recording, RecordingSource, Segment

WHEN = datetime(2026, 10, 30, tzinfo=UTC)


def _note(*recordings: tuple[str, ...]) -> Note:
    return Note(
        "n",
        "Note",
        WHEN,
        tuple(
            Recording(
                f"r{number}",
                WHEN,
                timedelta(seconds=9),
                RecordingSource.IMPORTED,
                "P",
                "M",
                tuple(Segment(text) for text in texts),
            )
            for number, texts in enumerate(recordings, start=1)
        ),
    )


def _found(note: Note, text: str, **options: bool) -> list[tuple[str, int, str]]:
    pattern = compile_query(FindQuery(text, **options))
    assert pattern is not None
    return [
        (m.recording_id, m.position, _segment(note, m)[m.start : m.end])
        for m in find_matches(note, pattern)
    ]


def _segment(note: Note, match: TextMatch) -> str:
    recording = note.recording(match.recording_id)
    assert recording is not None
    return recording.segments[match.position].text


def _replace(note: Note, text: str, replacement: str, **options: bool) -> dict[str, str]:
    query = FindQuery(text, **options)
    pattern = compile_query(query)
    assert pattern is not None
    result = replace_matches(
        note, pattern, find_matches(note, pattern), replacement, regex=query.regex
    )
    return {f"{rid}:{pos}": new for (rid, pos), new in result.texts.items()}


def test_an_empty_query_finds_nothing() -> None:
    assert compile_query(FindQuery("")) is None


def test_plain_text_ignores_case_and_treats_symbols_literally() -> None:
    note = _note(("Cat and CAT", "a.b axb"))

    assert _found(note, "cat") == [("r1", 0, "Cat"), ("r1", 0, "CAT")]
    assert _found(note, "a.b") == [("r1", 1, "a.b")]


def test_match_case_finds_only_the_same_case() -> None:
    assert _found(_note(("Cat and cat",)), "cat", match_case=True) == [("r1", 0, "cat")]


def test_whole_words_skips_words_inside_longer_words() -> None:
    note = _note(("cat category cat.", "C++ and C++11"))

    assert _found(note, "cat", whole_words=True) == [("r1", 0, "cat"), ("r1", 0, "cat")]
    assert _found(note, "C++", whole_words=True) == [("r1", 1, "C++")]


def test_matches_are_in_reading_order_across_recordings() -> None:
    note = _note(("b a", "a"), ("a",))

    assert _found(note, "a") == [("r1", 0, "a"), ("r1", 1, "a"), ("r2", 0, "a")]


def test_regular_expressions_skip_empty_matches() -> None:
    assert _found(_note(("xx y",)), "x*", regex=True) == [("r1", 0, "xx")]


def test_an_invalid_regular_expression_is_reported() -> None:
    with pytest.raises(InvalidPatternError):
        compile_query(FindQuery("(", regex=True))


def test_whole_words_cannot_turn_an_invalid_expression_into_a_valid_one() -> None:
    with pytest.raises(InvalidPatternError):
        compile_query(FindQuery("a)|(b", regex=True, whole_words=True))


def test_regular_expression_replacements_insert_groups() -> None:
    note = _note(("Smith John",))

    assert _replace(note, r"(\w+) (?P<first>\w+)", r"\g<first> \1", regex=True) == {
        "r1:0": "John Smith"
    }


def test_plain_replacements_are_inserted_as_written() -> None:
    assert _replace(_note(("cost",)), "cost", r"\1 $5") == {"r1:0": r"\1 $5"}


@pytest.mark.parametrize("replacement", [r"\2", r"\g<missing>"])
def test_a_replacement_with_a_missing_group_is_reported(replacement: str) -> None:
    note = _note(("Smith John",))
    with pytest.raises(InvalidReplacementError):
        _replace(note, r"(\w+)", replacement, regex=True)


def test_segments_that_would_become_empty_keep_their_text() -> None:
    note = _note(("um", "um, yes"))
    pattern = compile_query(FindQuery("um"))
    assert pattern is not None

    result = replace_matches(note, pattern, find_matches(note, pattern), "", regex=False)

    assert result.texts == {("r1", 1): ", yes"}
    assert (result.replaced, result.kept) == (1, 1)


def test_new_text_is_stripped_and_insertions_follow_the_strip() -> None:
    note = _note(("Hello there",))
    pattern = compile_query(FindQuery("Hello"))
    assert pattern is not None
    matches = find_matches(note, pattern)

    result = replace_matches(note, pattern, matches, "  Hi", regex=False)

    assert result.texts == {("r1", 0): "Hi there"}
    assert result.inserted == (TextMatch("r1", 0, 0, 2),)


def test_replacing_one_match_leaves_the_others() -> None:
    note = _note(("cat cat",))
    pattern = compile_query(FindQuery("cat"))
    assert pattern is not None
    second = find_matches(note, pattern)[1]

    result = replace_matches(note, pattern, (second,), "cats", regex=False)

    assert result.texts == {("r1", 0): "cat cats"}
    assert result.inserted == (TextMatch("r1", 0, 4, 8),)


def test_searching_on_after_a_replacement_skips_the_inserted_text() -> None:
    note = _note(("cat",), ("cat",))
    pattern = compile_query(FindQuery("cat"))
    assert pattern is not None
    first = find_matches(note, pattern)[0]
    result = replace_matches(note, pattern, (first,), "cats", regex=False)
    edited = _note(("cats",), ("cat",))
    inserted = result.inserted[0]

    index = first_match_from(
        edited, find_matches(edited, pattern), "r1", inserted.position, inserted.end
    )

    assert index == 1


def test_matches_that_are_no_longer_in_the_note_are_skipped() -> None:
    note = _note(("cat",))
    pattern = compile_query(FindQuery("cat"))
    assert pattern is not None
    stale = (TextMatch("r1", 0, 1, 4), TextMatch("gone", 0, 0, 3))

    result = replace_matches(note, pattern, stale, "dog", regex=False)

    assert (dict(result.texts), result.replaced) == ({}, 0)


def test_the_first_match_from_a_place_wraps_to_the_start() -> None:
    note = _note(("a a", "b"), ("a",))
    pattern = compile_query(FindQuery("a"))
    assert pattern is not None
    matches = find_matches(note, pattern)

    assert first_match_from(note, matches, "r1", 0, 0) == 0
    assert first_match_from(note, matches, "r1", 0, 1) == 1
    assert first_match_from(note, matches, "r1", 1, 0) == 2
    assert first_match_from(note, matches, "r2", 0, 1) == 0
    assert first_match_from(note, matches, "missing", 3, 3) == 0
    assert first_match_from(note, (), "r1", 0, 0) is None
