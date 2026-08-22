"""
Device token registration API.

This handles a per-device push credential, so it must be guarded by the
same `require_api_token` dependency as the briefing routes — see
app/api/devices.py's module docstring and tests/test_api_auth.py for why
that is non-negotiable in this project.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from tests.conftest import TEST_API_TOKEN

AUTH = {"X-Mercury-Token": TEST_API_TOKEN}


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("AUDIO_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def test_register_requires_token(client: TestClient):
    resp = client.post("/devices/register", json={"device_token": "abc123"})
    assert resp.status_code == 401


def test_delete_requires_token(client: TestClient):
    resp = client.delete("/devices/abc123")
    assert resp.status_code == 401


def test_register_then_list_persists_to_disk(client: TestClient, tmp_path):
    resp = client.post(
        "/devices/register", json={"device_token": "device-token-abc"}, headers=AUTH
    )
    assert resp.status_code == 200

    store = tmp_path / "device_tokens.json"
    assert store.exists()
    assert json.loads(store.read_text()) == ["device-token-abc"]


def test_register_is_idempotent(client: TestClient, tmp_path):
    for _ in range(3):
        resp = client.post(
            "/devices/register", json={"device_token": "same-token"}, headers=AUTH
        )
        assert resp.status_code == 200

    store = tmp_path / "device_tokens.json"
    assert json.loads(store.read_text()) == ["same-token"]


def test_register_rejects_empty_token(client: TestClient):
    resp = client.post("/devices/register", json={"device_token": "  "}, headers=AUTH)
    assert resp.status_code == 400


def test_unregister_removes_token(client: TestClient, tmp_path):
    client.post("/devices/register", json={"device_token": "to-remove"}, headers=AUTH)

    resp = client.delete("/devices/to-remove", headers=AUTH)
    assert resp.status_code == 200

    store = tmp_path / "device_tokens.json"
    assert json.loads(store.read_text()) == []


def test_unregister_unknown_token_is_404(client: TestClient):
    resp = client.delete("/devices/never-registered", headers=AUTH)
    assert resp.status_code == 404


def test_device_token_never_logged_in_full(client: TestClient, caplog):
    token = "super-secret-device-token-0123456789"
    with caplog.at_level("INFO"):
        client.post("/devices/register", json={"device_token": token}, headers=AUTH)
    for record in caplog.records:
        assert token not in record.getMessage()
