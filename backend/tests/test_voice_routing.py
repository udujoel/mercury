"""Voice settings must land in the variables the ACTIVE provider reads.

`UserSettings.voice_a/voice_b` are provider-neutral by name and were mapped
only to `ELEVENLABS_VOICE_*`, from a time when ElevenLabs was the default and
Google was a fallback nobody was meant to reach. Google became the default on
2026-09-12. Without this routing the picker would save, round-trip and change
nothing — the `music_set` bug in different clothes.
"""

import os

import pytest

from app.core.config import _BLANK_DEFAULTS, apply_to_environment
from app.core.schemas import UserSettings

_KEYS = ("TTS_PROVIDER", "ELEVENLABS_VOICE_A", "ELEVENLABS_VOICE_B",
         "TTS_VOICE_HOST_A", "TTS_VOICE_HOST_B")


@pytest.fixture(autouse=True)
def _clean_env():
    saved = {k: os.environ.get(k) for k in _KEYS}
    for k in _KEYS:
        os.environ.pop(k, None)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_the_server_default_is_google():
    """The decision, asserted where it is actually read."""
    assert _BLANK_DEFAULTS["tts_provider"] == "google"


def test_google_voices_reach_the_variables_google_tts_reads():
    apply_to_environment(
        UserSettings(tts_provider="google", voice_a="en-US-Neural2-C",
                     voice_b="en-US-Neural2-A"))
    assert os.environ["TTS_VOICE_HOST_A"] == "en-US-Neural2-C"
    assert os.environ["TTS_VOICE_HOST_B"] == "en-US-Neural2-A"
    assert "ELEVENLABS_VOICE_A" not in os.environ


def test_elevenlabs_voices_still_reach_the_elevenlabs_variables():
    """Rule 6: the provider can be swapped back the day someone funds it."""
    apply_to_environment(
        UserSettings(tts_provider="elevenlabs", voice_a="abc123", voice_b="def456"))
    assert os.environ["ELEVENLABS_VOICE_A"] == "abc123"
    assert os.environ["ELEVENLABS_VOICE_B"] == "def456"
    assert "TTS_VOICE_HOST_A" not in os.environ


@pytest.mark.parametrize(
    "alias", ["ElevenLabs", "eleven_labs", "11LABS", " eleven-labs "]
)
def test_provider_spelling_does_not_decide_where_a_voice_lands(alias):
    apply_to_environment(UserSettings(tts_provider=alias, voice_a="abc123"))
    assert os.environ["ELEVENLABS_VOICE_A"] == "abc123"
    assert "TTS_VOICE_HOST_A" not in os.environ


def test_a_null_provider_follows_the_server_default():
    """Null means "use the environment", and the environment now means Google.

    This is the case production is actually in: no TTS_PROVIDER secret is
    set, so an artifact that does not name a provider must still land its
    voices where the default provider will look.
    """
    apply_to_environment(UserSettings(voice_a="en-US-Neural2-C"))
    assert os.environ["TTS_VOICE_HOST_A"] == "en-US-Neural2-C"
    assert "ELEVENLABS_VOICE_A" not in os.environ
