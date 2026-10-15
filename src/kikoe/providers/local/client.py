"""The ``TranscriptionProvider`` for models that run on this device.

It knows nothing about downloads or settings: a ``LocalRuntime`` (the local model
manager) tells it where an installed model lives and which worker runs its engine.
"""

from datetime import timedelta
from pathlib import Path
from typing import Protocol

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
        connection = self._runtime.connection(spec.engine_id)
        return await connection.transcribe(
            path,
            audio,
            device=self._runtime.device(spec.engine_id),
            language=options.language,
            timestamps=options.timestamps and spec.timestamps,
        )

    async def open_realtime(
        self, model: ModelSpec, options: TranscriptionOptions
    ) -> RealtimeSession:
        raise TranscriptionError(_("This local model transcribes files only."))
