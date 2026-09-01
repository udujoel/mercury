"""
Storage I/O must not run on the event loop thread.

`BriefingStorage` is a synchronous Protocol, and the R2 implementation is
boto3 — blocking sockets, no async client. Called straight from an `async def`
route it holds the only event loop thread for the whole round trip, so one
slow or stalled R2 request makes every other request on the process
unresponsive. The audio `save` is the worst of them: ~3MB up, with the server
frozen for the duration.

ADR-008 means there is no hosted FastAPI backend today — the phone reads R2
through the Worker, and `uvicorn` is a local-development command. So this is
not currently a production outage. It is a trap laid for the day the app is
served anywhere, and the pipeline `save` already bites `POST /briefing/generate`
on a developer's machine.

Each test measures the same thing: while the blocking call is in flight, can
another coroutine still run?
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import pytest

from app.core.schemas import BriefingLength, BriefingManifest, Turn

BLOCK_S = 0.25
TICK_S = 0.01


def _manifest(briefing_id: str = "2026-08-21") -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=1000,
        audio_url=f"/briefing/{briefing_id}/audio",
        segments=[{"id": "cold_open", "label": "Good morning", "start_ms": 0}],
        turns=[Turn(speaker="A", segment="cold_open", text="Morning.")],
        sources=[],
    )


class _BlockingStorage:
    """Stands in for boto3 against a slow bucket: real, thread-blocking sleeps."""

    def audio_path_for(self, briefing_id: str) -> str:
        return f"/briefing/{briefing_id}/audio"

    def save(self, briefing_id, audio_bytes, audio_format, manifest) -> None:
        time.sleep(BLOCK_S)

    def load_manifest(self, briefing_id: str) -> BriefingManifest | None:
        time.sleep(BLOCK_S)
        return _manifest(briefing_id)

    def load_audio(self, briefing_id: str) -> tuple[bytes, str] | None:
        time.sleep(BLOCK_S)
        return b"audio", "audio/mp4"

    def list_briefing_ids(self) -> list[str]:
        return []

    def delete_briefing(self, briefing_id: str) -> None:
        pass

    def delete_audio(self, briefing_id: str) -> None:
        pass


async def _ticks_during(coro) -> int:
    """How many times a 10ms heartbeat gets to run while `coro` is awaited.

    Zero means the event loop was held by a blocking call for the whole of it,
    which is the defect. Anything close to BLOCK_S / TICK_S means the work was
    handed to a thread and the loop stayed responsive.
    """
    ticks = 0

    async def heartbeat() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(TICK_S)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        await coro
    finally:
        beat.cancel()
    return ticks


@pytest.fixture
def blocking_storage(monkeypatch):
    storage = _BlockingStorage()
    for module in (
        "app.api.briefing",
        "app.pipeline.generate",
        "app.scheduler.cron",
    ):
        monkeypatch.setattr(f"{module}.build_storage", lambda: storage, raising=True)
    return storage


# A blocking call that runs on the loop yields zero ticks. Half the ideal
# count is a wide margin that still fails hard on a regression.
MIN_TICKS = int(BLOCK_S / TICK_S) // 2


async def test_status_route_keeps_the_loop_free(blocking_storage):
    from app.api.briefing import status

    assert await _ticks_during(status("2026-08-21")) >= MIN_TICKS


async def test_manifest_route_keeps_the_loop_free(blocking_storage):
    from app.api.briefing import get_manifest

    assert await _ticks_during(get_manifest("2026-08-21")) >= MIN_TICKS


async def test_audio_route_keeps_the_loop_free(blocking_storage):
    from app.api.briefing import get_audio

    assert await _ticks_during(get_audio("2026-08-21")) >= MIN_TICKS


async def test_the_scheduled_job_keeps_the_loop_free(blocking_storage, monkeypatch):
    """The idempotency probe at the top of the job. It runs inside the same
    process as the API when the scheduler is mounted on the FastAPI app."""
    from app.scheduler.cron import run_daily_briefing_job

    ticks = await _ticks_during(
        run_daily_briefing_job(llm=object(), tts=object(), push_provider=None)
    )
    assert ticks >= MIN_TICKS


async def test_the_pipeline_save_keeps_the_loop_free(blocking_storage):
    """~3MB of audio up to R2. The longest blocking call in the system, and
    the one that runs while `POST /briefing/generate` is being polled."""
    from app.pipeline.generate import generate_briefing
    from app.providers.fake import FakeLLMProvider
    from app.providers.fake_tts import FakeTTSProvider
    from tests.test_generate_e2e import (
        FIXTURES_DIR,
        _fake_fetch_articles,
        _fake_fetch_events,
        _fake_fetch_weather,
        _fake_generate_script,
        _fake_score_emails,
        _fake_select_for_briefing,
    )

    ticks = await _ticks_during(
        generate_briefing(
            briefing_id="2026-08-21",
            length=BriefingLength.EXPRESS,
            llm=FakeLLMProvider(),
            tts=FakeTTSProvider(),
            fetch_events=_fake_fetch_events,
            fetch_weather=_fake_fetch_weather,
            fetch_articles=_fake_fetch_articles,
            score_emails=_fake_score_emails,
            select_for_briefing=_fake_select_for_briefing,
            generate_script=_fake_generate_script,
            storage=blocking_storage,
            gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
            calendar_fixture=None,
            weather_fixture=None,
        )
    )

    # The rest of the pipeline is already async, so almost every tick here is
    # from the save. Zero would mean the loop was held for the upload.
    assert ticks >= MIN_TICKS
