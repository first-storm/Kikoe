from datetime import timedelta

import pytest

from helpers import LIVE_ONLY
from kikoe.core.models import ApiKind, ModelSpec, Provider
from kikoe.core.transcription import CheckpointStrategy, RealtimeProfile
from kikoe.providers.factory import create_client

MODEL = ModelSpec("live", "Live", LIVE_ONLY)


@pytest.mark.parametrize(
    ("api", "profile"),
    [
        (ApiKind.OPENAI, RealtimeProfile(sample_rate=24000)),
        (ApiKind.OPENAI_COMPATIBLE, RealtimeProfile(sample_rate=24000)),
        (ApiKind.DEEPGRAM, RealtimeProfile(checkpoint=CheckpointStrategy.AUDIO_END)),
        (ApiKind.GEMINI, RealtimeProfile(max_session=timedelta(seconds=540))),
        (ApiKind.MISTRAL, RealtimeProfile()),
        (ApiKind.GROQ, RealtimeProfile()),
        (ApiKind.MIMO, RealtimeProfile()),
        (ApiKind.OPENROUTER, RealtimeProfile()),
    ],
)
def test_clients_report_the_realtime_profile_the_service_used_to_hardcode(
    api: ApiKind, profile: RealtimeProfile
) -> None:
    client = create_client(Provider("p", "Test", api, "https://example.test"), "key")
    assert client.realtime_profile(MODEL) == profile
