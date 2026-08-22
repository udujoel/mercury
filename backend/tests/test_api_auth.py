"""
Authentication on the briefing routes.

These endpoints serve the user's inbox and calendar as speech. During Phase 2
testing a cloudflared tunnel was opened so a physical iPhone could reach the
backend, and the full manifest — 44 turns, 66 source refs, built from a real
inbox — was fetchable by anyone holding the URL, because no route required a
credential. These tests exist so that cannot silently become true again.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth import MIN_TOKEN_LENGTH, generate_token
from app.core.config import get_settings
from app.main import app
from tests.conftest import TEST_API_TOKEN


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


PROTECTED = [
    ("get", "/briefing/2026-08-21"),
    ("get", "/briefing/2026-08-21/audio"),
    ("post", "/briefing/generate"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_no_token_is_rejected(client: TestClient, method: str, path: str):
    resp = getattr(client, method)(path)
    assert resp.status_code == 401, f"{method.upper()} {path} served without a token"


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_wrong_token_is_rejected(client: TestClient, method: str, path: str):
    resp = getattr(client, method)(path, headers={"X-Mercury-Token": "nope"})
    assert resp.status_code == 401


def test_correct_token_is_accepted(client: TestClient):
    # Not 401: the route runs. 404 for an unknown briefing id is fine — what
    # matters is that authentication no longer rejects it.
    resp = client.get(
        "/briefing/definitely-not-a-real-id",
        headers={"X-Mercury-Token": TEST_API_TOKEN},
    )
    assert resp.status_code != 401


def test_health_stays_public(client: TestClient):
    """Liveness must not require a secret — it exposes nothing personal and a
    guarded health check breaks every uptime probe."""
    assert client.get("/health").status_code == 200


def test_unset_token_refuses_to_serve(client: TestClient, monkeypatch):
    """Fail closed. An unconfigured server must refuse, not default to open —
    that default is precisely what published a real inbox."""
    monkeypatch.setenv("MERCURY_API_TOKEN", "")
    get_settings.cache_clear()

    resp = client.get(
        "/briefing/2026-08-21", headers={"X-Mercury-Token": TEST_API_TOKEN}
    )
    assert resp.status_code == 503
    assert "MERCURY_API_TOKEN" in resp.json()["detail"]


def test_short_token_refuses_to_serve(client: TestClient, monkeypatch):
    """A placeholder like "changeme" reads as configured and is worse than
    nothing, so a weak token is rejected as a misconfiguration."""
    monkeypatch.setenv("MERCURY_API_TOKEN", "short")
    get_settings.cache_clear()

    resp = client.get("/briefing/2026-08-21", headers={"X-Mercury-Token": "short"})
    assert resp.status_code == 503


def test_generated_token_meets_the_minimum():
    assert len(generate_token()) >= MIN_TOKEN_LENGTH
