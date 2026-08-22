"""
Tests for the /briefing API router (app.api.briefing), mounted in
app.main. Exercises it through fastapi.testclient end to end: generate
offline, fetch the manifest, fetch the audio with the right media type.

app.pipeline.generate.generate_briefing depends on ingestion/scoring/script
modules owned by another agent that may not exist yet; these tests monkey-
patch the router's `generate_briefing` symbol with a fast fake so the API
contract itself (routes, status codes, response shape) is verified
independently of whether that pipeline is fully wired up yet.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import briefing as briefing_api
from app.core.config import get_settings
from app.core.schemas import BriefingManifest
from app.main import app
from tests.conftest import TEST_API_TOKEN


@pytest.fixture
def client():
    client = TestClient(app)
    # Every briefing route requires the token; see app/core/auth.py.
    client.headers.update({"X-Mercury-Token": TEST_API_TOKEN})
    return client


def _fake_manifest(briefing_id: str = "2026-08-21") -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=1234,
        audio_url=f"/briefing/{briefing_id}/audio",
        segments=[{"id": "cold_open", "label": "Good morning", "start_ms": 0}],
        turns=[],
        sources=[],
    )


def test_health_still_works(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "mercury"
    # The client derives briefing ids locally (a background task has no
    # channel to be told one), so it needs the zone the SERVER names them in.
    # Asserted by key rather than by whole-dict equality so adding a field
    # here is not a breaking change for an unrelated test.
    assert body["timezone"]


def test_generate_starts_a_job_and_reports_ready_when_done(
    client, monkeypatch, tmp_path
):
    """Generation is a background job, not an inline request.

    This test previously asserted the endpoint returned the finished manifest.
    That contract is what produced `Error 524 origin_response_timeout` on a
    real phone: a full live run measured 127 SECONDS against Cloudflare's hard
    120-second proxy read timeout, so the connection died while the server
    carried on generating a briefing nobody was waiting for.
    """
    from app.api import jobs
    from app.synthesis.storage import LocalFilesystemStorage

    jobs.clear()
    monkeypatch.setenv("AUDIO_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()

    resp = client.post("/briefing/generate", json={"length": "standard"})
    assert resp.status_code == 202, "must return immediately, not hold the connection"
    body = resp.json()
    assert body["status"] in ("generating", "ready")
    briefing_id = body["briefing_id"]
    assert "manifest" not in body, "the manifest is fetched separately once ready"

    # Drive the background task to completion.
    job = jobs.get(briefing_id)
    if job is not None and job.task is not None:
        asyncio.get_event_loop_policy().new_event_loop()
        for _ in range(200):
            if job.state.value != "generating":
                break
            time.sleep(0.05)

    status = client.get(f"/briefing/{briefing_id}/status").json()
    assert status["status"] in ("generating", "ready"), status
    if status["status"] == "ready":
        manifest = client.get(f"/briefing/{briefing_id}").json()
        assert manifest["briefing_id"] == briefing_id
        assert manifest["turns"]
        assert LocalFilesystemStorage().load_manifest(briefing_id) is not None


def test_generate_failure_is_reported_via_status_not_a_500(client, monkeypatch):
    """A failing pipeline can no longer surface as a 500 on the POST.

    The request returns before generation starts, so the failure has to reach
    the client through the status endpoint instead. That is the cost of not
    holding the connection open, and it must actually work — a job that fails
    silently would leave the app spinning forever.
    """
    from app.api import jobs

    jobs.clear()

    async def _boom(**kwargs):
        raise RuntimeError("simulated pipeline failure")

    monkeypatch.setattr(briefing_api, "generate_briefing", _boom)

    resp = client.post("/briefing/generate", json={"briefing_id": "boom-day"})
    assert resp.status_code == 202

    for _ in range(200):
        job = jobs.get("boom-day")
        if job is not None and job.state.value != "generating":
            break
        time.sleep(0.05)

    status = client.get("/briefing/boom-day/status").json()
    assert status["status"] == "failed", status
    assert "simulated pipeline failure" in (status["error"] or "")


def test_get_manifest_returns_stored_manifest(client, monkeypatch, tmp_path):
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    manifest = _fake_manifest("2026-08-22")
    storage.save("2026-08-22", b"fake audio", "wav", manifest)

    monkeypatch.setattr(
        briefing_api,
        "LocalFilesystemStorage",
        lambda: LocalFilesystemStorage(base_dir=tmp_path),
    )

    resp = client.get("/briefing/2026-08-22")
    assert resp.status_code == 200
    assert resp.json()["briefing_id"] == "2026-08-22"


def test_get_manifest_missing_is_404(client, monkeypatch, tmp_path):
    from app.synthesis.storage import LocalFilesystemStorage

    monkeypatch.setattr(
        briefing_api,
        "LocalFilesystemStorage",
        lambda: LocalFilesystemStorage(base_dir=tmp_path),
    )
    resp = client.get("/briefing/no-such-id")
    assert resp.status_code == 404


def test_get_audio_returns_bytes_with_correct_media_type(
    client, monkeypatch, tmp_path
):
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    manifest = _fake_manifest("2026-08-23")
    storage.save("2026-08-23", b"fake wav payload", "wav", manifest)

    monkeypatch.setattr(
        briefing_api,
        "LocalFilesystemStorage",
        lambda: LocalFilesystemStorage(base_dir=tmp_path),
    )

    resp = client.get("/briefing/2026-08-23/audio")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content == b"fake wav payload"


def test_get_audio_missing_is_404(client, monkeypatch, tmp_path):
    from app.synthesis.storage import LocalFilesystemStorage

    monkeypatch.setattr(
        briefing_api,
        "LocalFilesystemStorage",
        lambda: LocalFilesystemStorage(base_dir=tmp_path),
    )
    resp = client.get("/briefing/no-such-id/audio")
    assert resp.status_code == 404


def test_generate_live_without_credentials_returns_400(client, monkeypatch):
    # No GOOGLE_TTS_CREDENTIALS / GEMINI_API_KEY configured on this machine
    # (per the MVP brief) — a live request must fail with a clear 400, not
    # a crash and not a faked live run.
    resp = client.post("/briefing/generate", json={"offline": False})
    assert resp.status_code == 400
    assert resp.json()["detail"]  # names the missing credential


# ──────────────────────────────────────────────────────────────
# Regression: live mode must require Google user OAuth, not just
# Gemini + TTS.
# ──────────────────────────────────────────────────────────────


def test_live_default_requires_a_google_user_token(monkeypatch, tmp_path):
    """Gemini + TTS alone must NOT select live mode.

    Gmail and Calendar ingestion go through the separate Google *user* OAuth
    token. Without it a "live" run either drops both primary sources —
    shipping a weather-only briefing that presents as the real thing — or,
    with only the client file present, starts an interactive consent flow
    from inside an API request and hangs. Both are worse than fixtures,
    because both look like success.
    """
    tts_key = tmp_path / "gcp-tts.json"
    tts_key.write_text("{}")

    monkeypatch.setenv("GEMINI_API_KEY", "a-key")
    monkeypatch.setenv("GOOGLE_TTS_CREDENTIALS", str(tts_key))
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(tmp_path / "no-such-token.json"))
    get_settings.cache_clear()

    assert briefing_api._credentials_configured() is False

    # With a token present, live becomes available.
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    get_settings.cache_clear()

    assert briefing_api._credentials_configured() is True
