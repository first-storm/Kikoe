"""Typed calls to one engine worker, and the user-facing meaning of its failures."""

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from kikoe.core.local_models import EngineDevice, EngineProbe
from kikoe.core.models import Segment
from kikoe.i18n import _
from kikoe.providers.base import TranscriptionError
from kikoe.providers.local.protocol import ErrorKind, Op
from kikoe.providers.local.worker_host import WorkerError, WorkerHost

if TYPE_CHECKING:
    from kikoe.providers.local.session import LocalSession


class EngineConnection:
    """One engine's worker; requests run one after another inside it."""

    def __init__(self, host: WorkerHost) -> None:
        self._host = host

    async def probe(self) -> EngineProbe:
        try:
            result = await self._host.request(Op.PROBE)
        except WorkerError as error:
            raise transcription_error(error) from error
        return EngineProbe(_string(result.get("version")), _devices(result.get("devices")))

    async def transcribe(
        self,
        model: Path,
        audio: Path,
        *,
        device: str | None,
        language: str | None,
        timestamps: bool,
    ) -> tuple[Segment, ...]:
        args: dict[str, object] = {
            "model": str(model),
            "audio": str(audio),
            "device": device,
            "language": language,
            "timestamps": timestamps,
        }
        try:
            result = await self._host.request(Op.TRANSCRIBE, args)
        except WorkerError as error:
            raise transcription_error(error) from error
        return parse_segments(result.get("segments"))

    async def open_stream(
        self, model: Path, *, device: str | None, language: str | None
    ) -> LocalSession:
        """A live transcription; its failures are ``TranscriptionError`` as well."""
        from kikoe.providers.local.session import LocalSession

        args: dict[str, object] = {"model": str(model), "device": device, "language": language}
        return await LocalSession.start(self._host, args)

    async def close(self) -> None:
        await self._host.close()


def parse_segments(value: object) -> tuple[Segment, ...]:
    """Segments from a worker reply; speaker numbers become recognition labels."""
    if not isinstance(value, list):
        raise TranscriptionError(_("The local engine returned an unreadable result."))
    value = cast(list[object], value)
    segments: list[Segment] = []
    for entry in _objects(value):
        text, speaker = entry.get("text"), entry.get("speaker")
        if not isinstance(text, str):
            continue
        segments.append(
            Segment(
                text,
                _milliseconds(entry.get("start_ms")),
                _milliseconds(entry.get("end_ms")),
                str(speaker) if type(speaker) is int else None,
            )
        )
    return tuple(segments)


def transcription_error(error: WorkerError) -> TranscriptionError:
    """Explain a worker failure; only a crashed or missing worker is worth retrying."""
    match error.kind:
        case ErrorKind.ENGINE:
            return TranscriptionError(
                _(
                    "The local engine could not start. Try another acceleration option in "
                    "Preferences, or download the engine again."
                ),
                retryable=True,
            )
        case ErrorKind.MODEL:
            return TranscriptionError(
                _(
                    "The model could not be loaded. It may need more memory than is free, "
                    "or the file is damaged; download it again or choose a smaller model."
                )
            )
        case ErrorKind.DEVICE:
            return TranscriptionError(
                _("The selected device failed. Choose another device in Preferences.")
            )
        case ErrorKind.AUDIO:
            return TranscriptionError(_("The local model could not process this audio."))
        case ErrorKind.CANCELLED:
            return TranscriptionError(_("Transcription was cancelled."))
        case ErrorKind.PROTOCOL | ErrorKind.FAILED:
            return TranscriptionError(_("The local engine failed. See the log for details."))


def _devices(value: object) -> tuple[EngineDevice, ...]:
    devices: list[EngineDevice] = []
    for entry in _objects(value):
        device_id, kind, name = (entry.get(k) for k in ("id", "kind", "name"))
        if isinstance(device_id, str) and isinstance(kind, str) and isinstance(name, str):
            devices.append(EngineDevice(device_id, kind, name))
    return tuple(devices)


def _objects(value: object) -> list[dict[str, object]]:
    """The JSON objects in a list, skipping anything else."""
    if not isinstance(value, list):
        return []
    items = cast(list[object], value)
    return [cast(dict[str, object], item) for item in items if isinstance(item, dict)]


def _milliseconds(value: object) -> timedelta | None:
    return timedelta(milliseconds=value) if type(value) is int and value >= 0 else None


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""
