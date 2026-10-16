"""Native speaker and timestamp switches survive the worker boundary independently."""

import wave
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import run_async
from kikoe.core.models import Segment
from kikoe.providers.local.connection import EngineConnection
from kikoe.providers.local.engines.transcribe_cpp.adapter import TranscribeCpp
from kikoe.providers.local.protocol import Op
from kikoe.providers.local.worker_host import WorkerHost


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, str, str]] = []

    def run(
        self, pcm: object, *, language: str | None, timestamps: str, diarize: str
    ) -> SimpleNamespace:
        self.calls.append((language, timestamps, diarize))
        return SimpleNamespace(
            text="Hello",
            timestamp_kind="segment" if timestamps == "auto" else "none",
            segments=[
                SimpleNamespace(
                    text="Hello", t0_ms=0, t1_ms=1000, speaker_id=1 if diarize == "on" else 0
                )
            ],
        )


class FakeModel:
    backend = "cpu"
    capabilities = SimpleNamespace(languages=("en", "zh"))

    def __init__(self, path: str, *, device: object = None) -> None:
        self.current_session = FakeSession()

    def session(self) -> FakeSession:
        return self.current_session


class AdapterHost(WorkerHost):
    """Run the real engine adapter against a fake binding without native downloads."""

    def __init__(self) -> None:
        super().__init__([])
        binding = SimpleNamespace(
            Model=FakeModel, backends=lambda: (), OutputTruncated=RuntimeError
        )
        self.adapter = TranscribeCpp(binding)
        self.args: dict[str, object] = {}

    async def request(
        self, op: Op, args: dict[str, object] | None = None, on_event: object = None
    ) -> dict[str, object]:
        self.args = args or {}
        return self.adapter.handle(op, self.args, lambda _: None)


@pytest.mark.parametrize("timestamps", [False, True])
@pytest.mark.parametrize("diarization", [False, True])
@run_async
async def test_worker_transcription_preserves_native_speakers_without_times(
    tmp_path: Path, timestamps: bool, diarization: bool
) -> None:
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 160)
    host = AdapterHost()
    transcript = await EngineConnection(host).transcribe(
        tmp_path / "model.gguf",
        audio,
        device=None,
        language="en-GB",
        timestamps=timestamps,
        diarization=diarization,
    )
    assert host.args["diarization"] is diarization
    assert host.adapter._session.calls == [
        ("en", "auto" if timestamps else "none", "on" if diarization else "off")
    ]
    assert transcript.segments == (
        Segment(
            "Hello",
            timedelta(0) if timestamps else None,
            timedelta(seconds=1) if timestamps else None,
            "1" if diarization else None,
        ),
    )
