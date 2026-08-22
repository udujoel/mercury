"""
APNs push provider.

No real APNs call is ever made here — see conftest.py's
`_no_ambient_credentials` and the hard-won-lessons rule against live,
billable calls from a test. `APNsProvider`'s construction-time config check
is exercised directly; the actual HTTP/2 send is only exercised through
`FakePushProvider`, which is what the scheduler tests use too.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.notifications.apns import (
    APNsConfigurationError,
    APNsProvider,
    FakePushProvider,
    PushProvider,
    _truncate_token,
)


def _settings(**overrides) -> Settings:
    base = {
        "apns_key_path": "",
        "apns_key_id": "",
        "apns_team_id": "",
        "apns_topic": "",
        "mercury_api_token": "x" * 32,
    }
    base.update(overrides)
    return Settings(**base)


def test_missing_config_raises_naming_every_missing_var():
    with pytest.raises(APNsConfigurationError) as exc_info:
        APNsProvider(settings=_settings())
    message = str(exc_info.value)
    for var in ("APNS_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_TOPIC"):
        assert var in message


def test_partial_config_names_only_what_is_missing():
    with pytest.raises(APNsConfigurationError) as exc_info:
        APNsProvider(
            settings=_settings(apns_key_id="ABC1234567", apns_team_id="DEF1234567")
        )
    message = str(exc_info.value)
    assert "APNS_KEY_PATH" in message
    assert "APNS_TOPIC" in message
    assert "APNS_KEY_ID" not in message
    assert "APNS_TEAM_ID" not in message


def test_missing_key_file_raises_clear_error(tmp_path):
    missing = tmp_path / "does-not-exist.p8"
    with pytest.raises(APNsConfigurationError) as exc_info:
        APNsProvider(
            settings=_settings(
                apns_key_path=str(missing),
                apns_key_id="ABC1234567",
                apns_team_id="DEF1234567",
                apns_topic="com.mercury.app",
            )
        )
    assert str(missing) in str(exc_info.value)


def test_never_fakes_a_send_on_bad_config():
    """A missing-config APNsProvider must raise at construction, never
    silently behave like it sent something."""
    with pytest.raises(APNsConfigurationError):
        APNsProvider(settings=_settings())


def test_truncate_token_never_exposes_the_full_token():
    token = "a" * 64
    truncated = _truncate_token(token)
    assert token not in truncated
    assert len(truncated) < len(token)


async def test_fake_push_provider_records_calls():
    fake = FakePushProvider()
    assert isinstance(fake, PushProvider)

    accepted = await fake.send_silent("device-token-1", {"briefing_id": "2026-08-22"})

    assert accepted is True
    assert fake.calls == [("device-token-1", {"briefing_id": "2026-08-22"})]


async def test_fake_push_provider_can_simulate_rejection():
    fake = FakePushProvider(should_succeed=False)
    accepted = await fake.send_silent("device-token-1", {})
    assert accepted is False
    # Still records the attempt even though it "failed".
    assert fake.calls == [("device-token-1", {})]
