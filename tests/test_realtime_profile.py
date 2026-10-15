from datetime import timedelta
from typing import cast

import pytest

from helpers import LIVE_ONLY
from kikoe.core.models import ApiKind, ModelSpec, Provider
from kikoe.core.transcription import CheckpointStrategy, RealtimeProfile
from kikoe.providers.factory import ClientContext, create_client
from kikoe.providers.local.client import LocalRuntime

MODEL = ModelSpec("live", "Live", LIVE_ONLY)


PROFILES = [
    (ApiKind.OPENAI, RealtimeProfile(sample_rate=24000)),
    (ApiKind.OPENAI_COMPATIBLE, RealtimeProfile(sample_rate=24000)),
    (ApiKind.DEEPGRAM, RealtimeProfile(checkpoint=CheckpointStrategy.AUDIO_END)),
    (ApiKind.GEMINI, RealtimeProfile(max_session=timedelta(seconds=540))),
    (ApiKind.MISTRAL, RealtimeProfile()),
    (ApiKind.GROQ, RealtimeProfile()),
    (ApiKind.MIMO, RealtimeProfile()),
    (ApiKind.OPENROUTER, RealtimeProfile()),
    (ApiKind.LOCAL, RealtimeProfile()),
]


def test_every_api_kind_declares_a_profile() -> None:
    assert {api for api, _profile in PROFILES} == set(ApiKind)


@pytest.mark.parametrize(("api", "profile"), PROFILES)
def test_each_client_reports_its_own_realtime_profile(
    api: ApiKind, profile: RealtimeProfile
) -> None:
    local = cast(LocalRuntime, object())  # Profiles do not depend on installed models.
    client = create_client(
        Provider("p", "Test", api, "https://example.test"), "key", ClientContext(local=local)
    )
    assert client.realtime_profile(MODEL) == profile
