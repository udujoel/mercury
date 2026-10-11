"""
SSRF guard.

`app/ingestion/rss.py` fetches whatever URL a feed entry's <link> contains.
That is attacker-controlled input: a hostile or compromised feed can point it
at the loopback interface or at cloud instance metadata, and without this
guard the response text is extracted straight into the briefing.
"""

from __future__ import annotations

import pytest

from app.core.net_guard import BlockedURLError, assert_public_url

BLOCKED = [
    # The two that matter most in practice.
    ("http://169.254.169.254/latest/meta-data/", "cloud instance metadata"),
    ("http://127.0.0.1:8000/briefing/2026-08-21", "our own API over loopback"),
    ("http://localhost:8000/health", "loopback by name"),
    ("http://10.0.0.5/admin", "RFC1918 private range"),
    ("http://192.168.1.1/", "home router"),
    ("http://172.16.4.4/", "RFC1918 private range"),
    ("http://[::1]/", "IPv6 loopback"),
    ("file:///etc/passwd", "non-HTTP scheme"),
    ("ftp://example.com/x", "non-HTTP scheme"),
]


@pytest.mark.parametrize(("url", "why"), BLOCKED)
def test_blocked(url: str, why: str):
    with pytest.raises(BlockedURLError):
        assert_public_url(url)


def test_public_url_allowed():
    assert_public_url("https://arstechnica.com/some-article")


def test_url_without_host_is_blocked():
    with pytest.raises(BlockedURLError):
        assert_public_url("http:///nohost")


def test_unresolvable_host_is_blocked():
    with pytest.raises(BlockedURLError):
        assert_public_url("http://this-host-does-not-exist.invalid/")


# ──────────────────────────────────────────────────────────────
# The suite's own keyless-fetch guard
# ──────────────────────────────────────────────────────────────


async def test_a_swallowed_live_fetch_is_still_recorded():
    """The guard raises, and every keyless source catches it.

    `rss.fetch_articles` gathers with `return_exceptions=True` and skips any
    BaseException; `weather.fetch_weather` returns None on `except
    Exception`. Both are correct under rule 7 and both mean the guard's
    AssertionError — the message naming the exact fix — reached nobody. A
    test that forgot `rss_fixture=` stayed off the network and then failed
    somewhere unrelated on missing content.

    This test trips the guard deliberately, so it drains the record; without
    the drain the conftest teardown would fail it.
    """
    from app.ingestion import rss, weather
    from tests.conftest import drain_live_fetch_attempts

    articles = await rss.fetch_articles(feeds=["https://example.com/feed.xml"])
    forecast = await weather.fetch_weather(lat=40.6, lon=-8.6)

    # Unchanged: the sources still degrade rather than raise.
    assert articles == []
    assert forecast is None

    # Changed: the attempt is no longer invisible.
    attempts = drain_live_fetch_attempts()
    assert len(attempts) == 2, attempts
    assert any("example.com/feed.xml" in a for a in attempts), attempts
    assert any("weather" in a for a in attempts), attempts


async def test_a_fixture_backed_run_is_not_accused_of_going_live():
    """The control that decides where the escalation goes.

    The RSS fixture carries feed XML, not article bodies, so a fixture-backed
    run still attempts full-text extraction through `safe_get` and still
    falls back to the feed summary — by design. Escalating every blocked
    `safe_get` would fail fourteen correct tests in
    `test_ingestion_rss.py`, so only the two helpers reachable exclusively on
    the `fixture=None` branch are recorded.
    """
    from datetime import UTC, datetime
    from pathlib import Path

    from app.ingestion import rss
    from tests.conftest import drain_live_fetch_attempts

    items = await rss.fetch_articles(
        feeds=[
            "https://example.com/feed/tech.xml",
            "https://example.com/feed/world.xml",
        ],
        fixture=Path(__file__).parent / "fixtures" / "rss_feeds.json",
        now=datetime(2026, 8, 21, tzinfo=UTC),
    )

    assert items, "the fixture should still produce items"
    assert drain_live_fetch_attempts() == []
