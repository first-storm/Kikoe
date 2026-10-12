"""Translated texts for core enums and validation codes."""

from datetime import datetime

from gi.repository import GLib

from kikoe.core.choice import unsupported_features
from kikoe.core.formatting import (
    RelativeDay,
    format_duration,
    relative_day,
    transcript_features,
)
from kikoe.core.models import (
    ApiKind,
    Capabilities,
    Note,
    NoteSummary,
    Recording,
    RecordingSource,
)
from kikoe.core.validation import Capability, ModelError, ProviderError, capability_list
from kikoe.i18n import _, ngettext
from kikoe.services.importer import ImportStage


def api_label(api: ApiKind) -> str:
    match api:
        case ApiKind.DEEPGRAM:
            return _("Deepgram")
        case ApiKind.OPENAI:
            return _("OpenAI")
        case ApiKind.OPENAI_COMPATIBLE:
            return _("OpenAI-compatible")


def capability_label(capability: Capability) -> str:
    match capability:
        case Capability.REALTIME:
            return _("Realtime")
        case Capability.TIMESTAMPS:
            return _("Timestamps")
        case Capability.DIARIZATION:
            return _("Speakers")


def capability_summary(capabilities: Capabilities) -> str:
    """E.g. “Realtime · Timestamps”, or “Text only” when the model has none."""
    labels = [capability_label(c) for c in capability_list(capabilities)]
    return " · ".join(labels) if labels else _("Text only")


def provider_error_message(error: ProviderError) -> str:
    match error:
        case ProviderError.EMPTY_NAME:
            return _("Enter a name for this provider.")
        case ProviderError.EMPTY_BASE_URL:
            return _("Enter the base URL of the API.")
        case ProviderError.INVALID_BASE_URL:
            return _("The base URL must start with http:// or https:// and include a host name.")


def model_error_message(error: ModelError) -> str:
    match error:
        case ModelError.EMPTY_MODEL_ID:
            return _("Enter the model ID.")
        case ModelError.DUPLICATE_MODEL_ID:
            return _("This provider already has a model with that ID.")


def day_label(when: datetime, now: datetime) -> str:
    """“Today 15:12”, “Yesterday”, “Oct 9” or “Oct 9, 2025” in the local time zone."""
    match relative_day(when, now):
        case RelativeDay.TODAY:
            # Translators: {time} is a time of day formatted with the “%H:%M” string below.
            return _("Today {time}").format(time=_format(when, _("%H:%M")))
        case RelativeDay.YESTERDAY:
            return _("Yesterday")
        case RelativeDay.THIS_YEAR:
            # Translators: date in this year, see the GLib.DateTime.format documentation.
            return _format(when, _("%b %-d"))
        case RelativeDay.OLDER:
            # Translators: date in another year, see the GLib.DateTime.format documentation.
            return _format(when, _("%b %-d, %Y"))


def note_subtitle(summary: NoteSummary, now: datetime) -> str:
    """Sidebar line, e.g. “Today 15:12 · 2 recordings” or “Oct 9 · Imported audio · 26:31”."""
    parts = [day_label(summary.created_at, now)]
    if summary.recording_count > 1:
        parts.append(_recording_count(summary.recording_count))
    elif summary.recording_count == 1:
        if summary.source is RecordingSource.IMPORTED:
            parts.append(_("Imported audio"))
        parts.append(format_duration(summary.duration))
    return " · ".join(parts)


def note_title_subtitle(note: Note) -> str:
    """Content header line, e.g. “2 recordings · 1:39”."""
    if not note.recordings:
        return _("No recordings")
    return f"{_recording_count(len(note.recordings))} · {format_duration(note.duration)}"


def _recording_count(count: int) -> str:
    return ngettext("{count} recording", "{count} recordings", count).format(count=count)


def _format(when: datetime, pattern: str) -> str:
    """Format with GLib, whose month names follow the UI locale on every platform.

    The time zone comes from Python so both agree even where GLib cannot find the
    system zone (e.g. some containers).
    """
    local = when.astimezone()
    offset = local.utcoffset()
    zone = GLib.TimeZone.new_offset(int(offset.total_seconds()) if offset else 0)
    moment = GLib.DateTime.new(
        zone, local.year, local.month, local.day, local.hour, local.minute, local.second
    )
    formatted = moment.format(pattern) if moment is not None else None
    return formatted or local.strftime("%Y-%m-%d %H:%M")


def recording_meta(recording: Recording, now: datetime) -> str:
    """Header line, e.g. “Today 14:30 · 0:58 · Groq whisper-large-v3 · Timestamps”."""
    parts = [
        day_label(recording.created_at, now),
        format_duration(recording.duration),
        f"{recording.provider_name} {recording.model_id}",
    ]
    if features := transcript_features(recording.segments):
        parts.append(", ".join(capability_label(feature) for feature in features))
    return " · ".join(parts)


def import_stage_label(stage: ImportStage, part: int, parts: int) -> str:
    """Progress text for the bottom bar, e.g. “Transcribing part 2 of 3…”."""
    match stage:
        case ImportStage.CONVERTING if parts > 1:
            return _("Converting part {part} of {parts}…").format(part=part, parts=parts)
        case ImportStage.CONVERTING:
            return _("Converting audio…")
        case ImportStage.TRANSCRIBING if parts > 1:
            return _("Transcribing part {part} of {parts}…").format(part=part, parts=parts)
        case ImportStage.TRANSCRIBING:
            return _("Transcribing…")
        case ImportStage.SAVING:
            return _("Saving…")


def chip_tooltip(capability: Capability, model: str, supported: bool, active: bool) -> str:
    """Tooltip of a Timestamps/Speakers chip in the bottom bar."""
    match capability, supported, active:
        case Capability.TIMESTAMPS, False, _:
            return _("{model} does not support timestamps").format(model=model)
        case Capability.DIARIZATION, False, _:
            return _("{model} does not support speaker detection").format(model=model)
        case Capability.TIMESTAMPS, True, True:
            return _("Generate timestamps while transcribing (click to turn off)")
        case Capability.TIMESTAMPS, True, False:
            return _("Generate timestamps while transcribing (click to turn on)")
        case Capability.DIARIZATION, True, True:
            return _("Detect speakers while transcribing (click to turn off)")
        case _:
            return _("Detect speakers while transcribing (click to turn on)")


def transcription_hint(capabilities: Capabilities) -> str:
    """E.g. “Transcribes after recording · This model does not support timestamps”."""
    missing = ", ".join(_feature_name(c) for c in unsupported_features(capabilities))
    match capabilities.realtime, bool(missing):
        case True, False:
            return _("Transcribes while recording")
        case False, False:
            return _("Transcribes after recording")
        case True, True:
            return _("Transcribes while recording · This model does not support {features}").format(
                features=missing
            )
        case False, True:
            return _("Transcribes after recording · This model does not support {features}").format(
                features=missing
            )


def _feature_name(capability: Capability) -> str:
    """Lower-case feature name for use inside a sentence."""
    match capability:
        case Capability.REALTIME:
            return _("realtime transcription")
        case Capability.TIMESTAMPS:
            return _("timestamps")
        case Capability.DIARIZATION:
            return _("speaker detection")
