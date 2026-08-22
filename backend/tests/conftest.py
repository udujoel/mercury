"""
Shared test guards.

The suite must behave identically whether or not the developer has real
credentials sitting in backend/.env. Without this, `pytest` silently changes
meaning the moment you configure the app: a test written to assert "a live
request fails because nothing is configured" instead performs a REAL live
generation — two Gemini passes plus a Google TTS call per turn — which is
slow, billable, and no longer testing what it claims to.

That is not hypothetical. It is exactly what happened here the first time
credentials landed on this machine: `test_api.py` hung indefinitely mid-run.
"""

from __future__ import annotations

import pytest

from app.core.config import get_settings

CREDENTIAL_ENV_VARS = (
    "GEMINI_API_KEY",
    "GOOGLE_TTS_CREDENTIALS",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "ELEVENLABS_API_KEY",
)

# Google's OAuth helper resolves these to real files at the repo root once the
# developer has run a live spike. Pointing them at a path that cannot exist
# keeps "live path without credentials" tests deterministic — and, more
# importantly, makes it impossible for a test run to open a real consent
# browser window or spend a real API call.
MISSING_PATH = "/nonexistent/mercury-tests/credentials.json"

# Not a credential to strip — it is OUR auth token, and the API fails closed
# without one. Pin it so API tests exercise the authenticated path rather than
# every route 503-ing. Tests that care about the unconfigured case override it.
TEST_API_TOKEN = "test-token-" + "x" * 32
CREDENTIAL_PATH_ENV_VARS = (
    "GOOGLE_CREDENTIALS_FILE",
    "GOOGLE_TOKEN_FILE",
)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """Strip real credentials from the environment for every test.

    Anything that genuinely needs to exercise a live path must inject its own
    provider explicitly, so the intent is visible in the test rather than
    inherited from whatever happens to be on the machine.
    """
    # setenv("") rather than delenv(): pydantic-settings resolves environment
    # variables ahead of the .env file, so deleting a var just lets the real
    # value in backend/.env through. An explicit empty value overrides it.
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(var, "")
    for var in CREDENTIAL_PATH_ENV_VARS:
        monkeypatch.setenv(var, MISSING_PATH)
    monkeypatch.setenv("MERCURY_API_TOKEN", TEST_API_TOKEN)

    # Settings is lru_cached, so a cached instance built from a real .env
    # would leak straight past the monkeypatch above.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
