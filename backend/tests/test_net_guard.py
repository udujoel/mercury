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
