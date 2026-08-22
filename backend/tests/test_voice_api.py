"""
Tests for app/api/voice.py — POST /voice/ask, the Phase 3 streaming
voice-interrupt endpoint.

Guarded with the same `require_api_token` dependency as every other route
touching personal data (see tests/test_api_auth.py) — a voice question
carries the listener's spoken transcript plus inbox/calendar-derived
context, so it fails closed the same way.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.schemas import (
    BriefingManifest,
    Segment,
    SourceRef,
    SourceType,
    Turn,
)
from app.main import app
from app.synthesis.storage import LocalFilesystemStorage
from tests.conftest import TEST_API_TOKEN

AUTH = {"X-Mercury-Token": TEST_API_TOKEN}
BRIEFING_ID = "2026-08-21"


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("AUDIO_OUTPUT_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield TestClient(app)
    get_settings.cache_clear()


def _seed_manifest(tmp_path) -> None:
    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning, Joel.",
             start_ms=0, end_ms=10_000),
        Turn(speaker="B", segment=Segment.INBOX,
             text="Speaking of Priya, she emailed the rollout plan.",
             start_ms=10_200, end_ms=40_000),
    ]
    sources = [
        SourceRef(turn_index=1, title="Rollout plan sign-off", url=None,
                   source_name="Priya Sharma", source_type=SourceType.EMAIL),
    ]
    manifest = BriefingManifest(
        briefing_id=BRIEFING_ID,
        generated_at="2026-08-21T07:00:00+00:00",
        duration_ms=40_000,
        audio_url=f"/briefing/{BRIEFING_ID}/audio",
        segments=[],
        turns=turns,
        sources=sources,
    )
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save(BRIEFING_ID, b"fake-audio-bytes", "wav", manifest)


def _ask_body(**overrides) -> dict:
    body = {
        "briefing_id": BRIEFING_ID,
        "question": "What does Priya's plan say?",
        "position_ms": 20_000,
        "offline": True,
    }
    body.update(overrides)
    return body


def _read_ndjson(resp) -> list[dict]:
    lines = [line for line in resp.text.splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


# ──────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────


def test_ask_requires_token(client: TestClient, tmp_path):
    _seed_manifest(tmp_path)
    resp = client.post("/voice/ask", json=_ask_body())
    assert resp.status_code == 401


def test_ask_wrong_token_is_rejected(client: TestClient, tmp_path):
    _seed_manifest(tmp_path)
    resp = client.post(
        "/voice/ask", json=_ask_body(), headers={"X-Mercury-Token": "nope"}
    )
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────
# Request validation
# ──────────────────────────────────────────────────────────────


def test_ask_unknown_briefing_is_404(client: TestClient):
    resp = client.post(
        "/voice/ask", json=_ask_body(briefing_id="never-generated"), headers=AUTH
    )
    assert resp.status_code == 404


def test_ask_empty_question_is_400(client: TestClient, tmp_path):
    _seed_manifest(tmp_path)
    resp = client.post("/voice/ask", json=_ask_body(question="   "), headers=AUTH)
    assert resp.status_code == 400


def test_ask_negative_position_ms_is_400(client: TestClient, tmp_path):
    _seed_manifest(tmp_path)
    resp = client.post("/voice/ask", json=_ask_body(position_ms=-1), headers=AUTH)
    assert resp.status_code == 400


# ──────────────────────────────────────────────────────────────
# Streaming happy path
# ──────────────────────────────────────────────────────────────


def test_ask_streams_ndjson_with_audio_and_final_timing(client: TestClient, tmp_path):
    _seed_manifest(tmp_path)
    resp = client.post("/voice/ask", json=_ask_body(), headers=AUTH)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    frames = _read_ndjson(resp)
    assert frames, "expected at least one NDJSON frame"

    # Every frame is well-formed and its audio (when present) decodes.
    for frame in frames:
        assert "kind" in frame and "speaker" in frame and "text" in frame
        if frame["audio_b64"]:
            audio = base64.b64decode(frame["audio_b64"])
            assert len(audio) > 0

    # Exactly one frame carries the timing budget instrumentation, and it is
    # the LAST one -- the client should not have to guess when the stream is
    # actually finished.
    timed = [f for f in frames if f.get("timing") is not None]
    assert len(timed) == 1
    assert frames[-1] is timed[0]
    timing = timed[0]["timing"]
    timing_keys = (
        "llm_first_token_ms", "first_sentence_ms", "first_audio_ms", "total_ms",
    )
    for key in timing_keys:
        assert key in timing
        assert timing[key] is not None
        assert timing[key] >= 0


def test_ask_answer_depends_on_the_question_asked(client: TestClient, tmp_path):
    """End-to-end proof (through the real HTTP layer, not just the pipeline
    unit tests) that the answer text actually reflects the question -- not a
    fixed response regardless of input."""
    _seed_manifest(tmp_path)

    resp_weather = client.post(
        "/voice/ask", json=_ask_body(question="What's the weather like?"),
        headers=AUTH,
    )
    resp_plan = client.post(
        "/voice/ask", json=_ask_body(question="What does Priya's plan say?"),
        headers=AUTH,
    )

    weather_text = "".join(
        f["text"] for f in _read_ndjson(resp_weather) if f["kind"] == "answer"
    )
    plan_text = "".join(
        f["text"] for f in _read_ndjson(resp_plan) if f["kind"] == "answer"
    )
    assert weather_text != plan_text


def test_ask_bridge_is_the_last_spoken_frame(client: TestClient, tmp_path):
    _seed_manifest(tmp_path)
    resp = client.post("/voice/ask", json=_ask_body(), headers=AUTH)
    frames = _read_ndjson(resp)
    spoken = [f for f in frames if f["kind"] in ("answer", "bridge")]
    assert spoken
    assert spoken[-1]["kind"] == "bridge"


def test_ask_missing_live_credentials_is_400(client: TestClient, tmp_path):
    """offline=False with no Gemini/TTS credentials configured (the test
    environment always strips them -- see conftest._no_ambient_credentials)
    must fail with a client error naming the fix, not a 500."""
    _seed_manifest(tmp_path)
    resp = client.post(
        "/voice/ask", json=_ask_body(offline=False), headers=AUTH,
    )
    assert resp.status_code == 400
