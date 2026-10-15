"""The ``TranscriptionProvider`` for models that run on this device.

It knows nothing about downloads or settings: a ``LocalRuntime`` (the local model
manager) tells it where an installed model lives and which worker runs its engine.
"""

from datetime import timedelta
from pathlib import Path
from typing import Protocol

from kikoe.core.diarization import assign_speakers
from kikoe.core.local_models import LocalModelSpec
from kikoe.core.models import ModelSpec, Segment
from kikoe.core.transcription import RealtimeProfile
from kikoe.core.upload import WAV, UploadLimits
from kikoe.i18n import _
from kikoe.providers.base import (
    FileTranscriptCallback,
    RealtimeSession,
    TranscriptionError,
    TranscriptionOptions,
)
from kikoe.providers.local.connection import EngineConnection


class LocalRuntime(Protocol):
    def installed_model(self, model_id: str) -> tuple[LocalModelSpec, Path] | None:
        """The catalog entry and file of an installed model; None if it is gone."""
        ...

    def connection(self, engine_id: str) -> EngineConnection:
        """Raises ``TranscriptionError`` when the engine is not installed."""
        ...

    def device(self, engine_id: str) -> str | None: ...

    def diarizer(self, engine_id: str) -> Path | None:
        """The installed speaker detection model of the engine, if any."""
        ...


class LocalClient:
    def __init__(self, runtime: LocalRuntime) -> None:
        self._runtime = runtime

    def _model(self, model: ModelSpec) -> tuple[LocalModelSpec, Path]:
        found = self._runtime.installed_model(model.model_id)
        if found is None:
            raise TranscriptionError(
                _("“{model}” is no longer downloaded. Download it again in Preferences.").format(
                    model=model.display_name
                )
            )
        return found

    def upload_limits(self, model: ModelSpec, options: TranscriptionOptions) -> UploadLimits:
        found = self._runtime.installed_model(model.model_id)
        seconds = found[0].max_seconds if found else None
        return UploadLimits(duration=timedelta(seconds=seconds) if seconds else None, format=WAV)

    def realtime_profile(self, model: ModelSpec) -> RealtimeProfile:
        return RealtimeProfile()

    async def transcribe_file(
        self,
        audio: Path,
        model: ModelSpec,
        options: TranscriptionOptions,
        *,
        on_update: FileTranscriptCallback | None = None,
    ) -> tuple[Segment, ...]:
        spec, path = self._model(model)
        engine = spec.engine_id
        connection = self._runtime.connection(engine)
        device = self._runtime.device(engine)
        diarizer = self._runtime.diarizer(engine) if options.diarization else None
        # Speakers are matched by time, so they need times even when not shown.
        speakers = diarizer is not None and spec.timestamps
        transcript = await connection.transcribe(
            path,
            audio,
            device=device,
            language=options.language,
            timestamps=(options.timestamps or speakers) and spec.timestamps,
            words=speakers,
        )
        if diarizer is None or not speakers:
            return transcript.segments
        turns = await connection.diarize(diarizer, audio, device=device)
        return assign_speakers(transcript.segments, transcript.words, turns)

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        spec, path = self._model(model)
        if not spec.streaming:
            raise TranscriptionError(_("This local model transcribes files only."))
        connection = self._runtime.connection(spec.engine_id)
        return await connection.open_stream(
            path, device=self._runtime.device(spec.engine_id), language=options.language
        )
