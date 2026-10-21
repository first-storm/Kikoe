"""The bundled presets are the specification of what each endpoint accepts."""

import pytest

from kikoe.core.model_catalog import CatalogError, ModelCatalog, Support, parse_catalog
from kikoe.core.models import ApiKind, Capabilities, ModeCapabilities, ModelSpec
from kikoe.storage.model_catalog import load_catalog

ALL_ON = Capabilities(
    file=ModeCapabilities(True, True, True), realtime=ModeCapabilities(True, True, True)
)


@pytest.fixture(scope="module")
def catalog() -> ModelCatalog:
    return load_catalog()


@pytest.mark.parametrize(
    ("api", "id", "file", "realtime", "timestamps", "speakers"),
    [
        (ApiKind.OPENAI, "gpt-live-transcribe", False, True, False, False),
        (ApiKind.OPENAI, "gpt-4o-transcribe", True, True, False, False),
        (ApiKind.OPENAI, "whisper-1", True, False, True, False),
        (ApiKind.OPENAI, "gpt-4o-transcribe-diarize", True, False, True, True),
        (ApiKind.OPENROUTER, "openai/whisper-1", True, False, True, True),
        (ApiKind.GEMINI, "gemini-3.5-transcribe-live", False, True, False, False),
        (ApiKind.GEMINI, "gemini-3.5-transcribe", True, False, True, True),
        (ApiKind.DEEPGRAM, "nova-3", True, True, True, True),
        (ApiKind.MIMO, "mimo-v2.5-asr", True, False, False, False),
        (ApiKind.AUDIO_LLM, "gpt-4o-audio-preview", True, False, False, False),
        (ApiKind.MISTRAL, "voxtral-mini-latest", True, False, True, True),
        (ApiKind.MISTRAL, "voxtral-mini-transcribe-realtime-2602", False, True, False, False),
        (ApiKind.MISTRAL, "voxtral-small-transcribe-3-0", True, False, True, True),
        (ApiKind.MISTRAL, "voxtral-mini-transcribe-3-0", True, False, True, True),
        (ApiKind.MISTRAL, "voxtral-mini-transcribe-realtime-3-0", False, True, False, False),
    ],
)
def test_known_api_limits_override_manual_capability_claims(
    catalog: ModelCatalog,
    api: ApiKind,
    id: str,
    file: bool,
    realtime: bool,
    timestamps: bool,
    speakers: bool,
) -> None:
    capabilities = catalog.clamp(api, ModelSpec(id, id, ALL_ON)).capabilities
    assert (capabilities.file is not None) is file
    assert (capabilities.realtime is not None) is realtime
    assert capabilities.recording.timestamps is timestamps
    assert capabilities.recording.diarization is speakers


@pytest.mark.parametrize(
    "model_id",
    [
        "gpt-transcribe",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
        "gpt-4o-mini-transcribe-2025-12-15",
        "gpt-4o-transcribe-diarize",
    ],
)
def test_openai_streaming_models_stream_by_default_only_on_the_official_api(
    catalog: ModelCatalog, model_id: str
) -> None:
    official = catalog.preset(ApiKind.OPENAI, model_id).defaults().file
    compatible = catalog.preset(ApiKind.OPENAI_COMPATIBLE, model_id).file
    assert official is not None and official.streaming
    assert compatible.streaming is Support.OFF


def test_unknown_models_get_their_api_fallback(catalog: ModelCatalog) -> None:
    preset = catalog.preset(ApiKind.OPENAI_COMPATIBLE, "whisper-large-v3")
    assert preset == catalog.apis[ApiKind.OPENAI_COMPATIBLE]
    assert catalog.preset(ApiKind.OPENAI, "whisper-large-v3").defaults().file == ModeCapabilities(
        timestamps=True
    )


def test_mimo_streams_by_default_but_preserves_existing_switches(catalog: ModelCatalog) -> None:
    preset = catalog.preset(ApiKind.MIMO, "mimo-v2.5-asr")
    assert preset.defaults().file == ModeCapabilities(streaming=True)
    for streaming in (False, True):
        model = ModelSpec(
            "mimo-v2.5-asr", "MiMo", Capabilities(file=ModeCapabilities(streaming=streaming))
        )
        assert catalog.clamp(ApiKind.MIMO, model) == model


def test_audio_llm_models_stream_by_default_and_never_claim_timestamps(
    catalog: ModelCatalog,
) -> None:
    preset = catalog.preset(ApiKind.AUDIO_LLM, "any-audio-model")
    assert preset.defaults().file == ModeCapabilities(streaming=True)
    assert preset.defaults().realtime is None


def test_a_model_left_without_any_mode_gets_the_defaults(catalog: ModelCatalog) -> None:
    model = ModelSpec("gpt-live-transcribe", "Live", Capabilities())
    assert catalog.clamp(ApiKind.OPENAI, model).capabilities == Capabilities(
        file=None, realtime=ModeCapabilities()
    )


def _document(models: list[object]) -> dict[str, object]:
    file = {"available": "on"}
    apis = {api.value: {"file": file} for api in ApiKind}
    return {"version": 1, "apis": apis, "models": models}


def test_rules_match_in_order_and_inherit_unset_fields() -> None:
    catalog = parse_catalog(
        _document(
            [
                {"apis": ["deepgram"], "ids": ["nova-3"], "file": {"streaming": "on"}},
                {"apis": ["deepgram"], "prefixes": ["nova"], "file": {"timestamps": "on"}},
            ]
        )
    )
    exact = catalog.preset(ApiKind.DEEPGRAM, "nova-3").file
    prefixed = catalog.preset(ApiKind.DEEPGRAM, "nova-2").file
    assert (exact.available, exact.streaming, exact.timestamps) == (
        Support.ON,
        Support.ON,
        Support.OFF,
    )
    assert prefixed.timestamps is Support.ON
    assert catalog.preset(ApiKind.OPENAI, "nova-3").file.timestamps is Support.OFF


@pytest.mark.parametrize(
    "rule",
    [
        {"apis": ["deepgram"], "ids": ["x"], "realtime": {"streaming": "on"}},
        {"apis": ["deepgram"], "ids": ["x"], "file": {"timestamps": "maybe"}},
        {"apis": ["deepgram"], "ids": ["x"], "file": {"available": "unsupported"}},
        {"apis": ["deepgram"]},
        {"apis": ["nonsense"], "ids": ["x"]},
    ],
)
def test_malformed_rules_are_rejected(rule: dict[str, object]) -> None:
    with pytest.raises(CatalogError):
        parse_catalog(_document([rule]))


def test_every_api_needs_an_entry() -> None:
    document = _document([])
    apis = document["apis"]
    assert isinstance(apis, dict)
    del apis["gemini"]
    with pytest.raises(CatalogError):
        parse_catalog(document)


def test_mistral_streams_file_previews_by_default(catalog: ModelCatalog) -> None:
    file = catalog.preset(ApiKind.MISTRAL, "voxtral-mini-latest").defaults().file
    assert file == ModeCapabilities(timestamps=True, diarization=True, streaming=True)
