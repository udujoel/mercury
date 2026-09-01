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


@pytest.fixture(autouse=True)
def _no_live_keyless_fetches(monkeypatch):
    """Make the keyless sources' outbound HTTP impossible for every test.

    Gmail and Calendar fail closed without credentials, which is what has
    kept the suite honest so far. RSS and weather do not: both are keyless,
    so `fixture=None` means "go and fetch the real internet" and nothing
    above them says no. The moment `fetch_articles` was wired into
    `generate_briefing`, every end-to-end test started making real requests
    to real publishers — the run went from 24 seconds to two minutes, and
    its results depended on what the BBC had published that morning. Weather
    was the same hole, one source over.

    Patched on the `app.ingestion.*` bindings rather than in
    `app.core.net_guard`, so `test_net_guard.py` still exercises the guard
    itself and tests that monkeypatch these helpers directly are unaffected.

    Two tiers, because raising is not enough on its own and blanket
    escalation is wrong:

    `_fetch_feed_live` and `weather._fetch_live` are reachable ONLY on the
    `fixture=None` branch, so reaching them means a test forgot its fixture.
    Their refusal is recorded and re-raised at teardown, because every
    caller swallows it — `fetch_articles` gathers with
    `return_exceptions=True` and skips any BaseException, `fetch_weather`
    returns None on `except Exception` (rule 7, correctly, in both cases) —
    so the AssertionError naming the exact fix reached nobody and the test
    failed somewhere unrelated on missing content.

    `safe_get` stays blocked but NOT escalated. `_extract_full_text` goes
    through it, and the RSS fixture carries feed XML rather than article
    bodies, so every fixture-backed test legitimately attempts full-text
    extraction and legitimately falls back to the feed summary. Escalating
    that fails fourteen correct tests.
    """
    from app.ingestion import rss, weather

    _live_fetch_attempts.clear()

    def _refusing(source: str, *, escalate: bool):
        async def _refuse(*args, **kwargs):
            if escalate:
                url = next((a for a in args if isinstance(a, str)), None)
                _live_fetch_attempts.append(f"{source} {url}" if url else source)
            raise AssertionError(
                f"a test let {source} run against the live internet. Pass "
                "fixture=<path> (rss_fixture=/weather_fixture= to "
                "generate_briefing), or monkeypatch the live fetch helper."
            )

        return _refuse

    monkeypatch.setattr(rss, "safe_get", _refusing("an RSS fetch", escalate=False))
    monkeypatch.setattr(
        rss, "_fetch_feed_live", _refusing("an RSS feed fetch", escalate=True)
    )
    monkeypatch.setattr(
        weather, "_fetch_live", _refusing("a weather fetch", escalate=True)
    )
    yield
    if _live_fetch_attempts:
        attempted = list(_live_fetch_attempts)
        _live_fetch_attempts.clear()
        pytest.fail(
            f"this test ran a keyless ingestion source live: {attempted}. The "
            "guard blocked the request, but the source swallowed the refusal "
            "(rule 7), so the test saw an empty result rather than an error "
            "and will have failed somewhere unrelated. Pass fixture=<path> "
            "(rss_fixture=/weather_fixture= to generate_briefing), or "
            "monkeypatch the live fetch helper. A test that means to trip "
            "the guard should call tests.conftest.drain_live_fetch_attempts()."
        )


# Every live fetch the guard above refused during the current test. Module
# level rather than a fixture value because the callers that swallow the
# refusal are several frames down and have no access to the fixture.
_live_fetch_attempts: list[str] = []


def drain_live_fetch_attempts() -> list[str]:
    """Take and clear the recorded attempts.

    For the one test that trips the guard ON PURPOSE, to prove the recording
    works. Draining it is what stops the teardown above failing that test.
    """
    attempts = list(_live_fetch_attempts)
    _live_fetch_attempts.clear()
    return attempts
