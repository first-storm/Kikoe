from pathlib import Path

import pytest

from kikoe.core.drop import DropRejection, dropped_audio


@pytest.mark.parametrize("name", ["talk.mp3", "talk.wav", "Meeting.M4A", "clip.mp4", "clip.webm"])
def test_one_audio_or_video_file_is_imported(name: str) -> None:
    path = Path("recordings") / name
    assert dropped_audio([path]) == path


def test_several_files_are_refused_even_when_all_are_audio() -> None:
    assert dropped_audio([Path("a.mp3"), Path("b.mp3")]) is DropRejection.SEVERAL


def test_an_empty_drop_is_refused() -> None:
    assert dropped_audio([]) is DropRejection.SEVERAL


def test_a_file_that_is_not_on_this_computer_is_refused() -> None:
    assert dropped_audio([None]) is DropRejection.NOT_LOCAL


@pytest.mark.parametrize("name", ["notes.txt", "paper.pdf", "photo.PNG"])
def test_a_file_known_not_to_be_media_is_refused(name: str) -> None:
    assert dropped_audio([Path(name)]) is DropRejection.NOT_MEDIA


@pytest.mark.parametrize("name", ["voice.kikoeaudio", "recording"])
def test_an_unknown_extension_is_left_for_ffmpeg_to_judge(name: str) -> None:
    assert dropped_audio([Path(name)]) == Path(name)
