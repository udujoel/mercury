"""
Server-side generation cron + delivery push.

This is the PRIMARY layer of the three-layer delivery strategy (ADR-001,
docs/ROADMAP.md Phase 2 week 11): a cron job runs `generate_briefing` daily
at the user's wake time minus a lead, then sends a silent push hinting the
app that a new briefing is ready. The BGProcessingTask prefetch (SECONDARY)
and foreground fetch-on-launch (FALLBACK) are iOS-side and are what actually
guarantee the user gets the briefing — see app/notifications/apns.py's
docstring for why the push itself is only a hint.

Timezone correctness is the entire point of this module. A previous bug had
`datetime.now(UTC)` at 00:11 in Europe/Lisbon read as the *previous* UTC
date, so the cold open announced the wrong day while the calendar segment
(built from real events) talked about the right one — the hosts contradicted
each other inside one briefing (see app/pipeline/generate.py's
`_parse_briefing_date` for where that was fixed on the generation side).
Here it means two things: the fire time is computed and scheduled in
`settings.user_timezone`, never UTC, and the "is today's briefing already
made" check uses today's date *in that timezone* too.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.providers import LLMProvider, TTSProvider
from app.core.schemas import BriefingLength
from app.notifications.apns import (
    APNsConfigurationError,
    PushProvider,
    TokenUnregisteredError,
)
from app.pipeline.generate import generate_briefing
from app.synthesis.storage import BriefingStorage, LocalFilesystemStorage

log = logging.getLogger(__name__)

JOB_ID = "mercury-daily-briefing"

# Module-level so start_scheduler/shutdown_scheduler can be called from the
# FastAPI lifespan without threading a handle through app.state everywhere
# that wants it (tests do want it — see get_scheduler()).
_scheduler: AsyncIOScheduler | None = None


def _compute_fire_time(wake_time: str, lead_minutes: int) -> tuple[int, int, bool]:
    """(hour, minute, crosses_midnight) the job should fire, local clock.

    Pure clock arithmetic on an arbitrary anchor date — no timezone, no UTC
    conversion. The CronTrigger this feeds is given `settings.user_timezone`
    directly and is what resolves the actual fire *date* (including DST
    transitions); this function only needs the time of day.

    `crosses_midnight` matters because the briefing is named for the day the
    user WAKES, not the day the job runs. With wake_time 00:30 and a 60
    minute lead the job fires at 23:30 the previous evening, and stamping it
    with that evening's date filed the briefing one day early — the client,
    correctly asking for the wake date, got a 404 and the whole scheduled
    delivery silently failed.
    """
    hour_str, minute_str = wake_time.split(":")
    anchor = datetime(2000, 1, 2, int(hour_str), int(minute_str))
    fire = anchor - timedelta(minutes=lead_minutes)
    return fire.hour, fire.minute, fire.day != anchor.day


def _user_tz(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        log.error("invalid user_timezone %r, falling back to UTC", tz_name)
        return ZoneInfo("UTC")


def _target_briefing_id(tz_name: str, *, crosses_midnight: bool = False) -> str:
    """The id of the briefing this run is preparing, in the USER's timezone.

    Normally today's date. When the generation lead pushes the run back past
    midnight the briefing is for TOMORROW's wake, so the id advances — see
    `_compute_fire_time`.
    """
    now = datetime.now(_user_tz(tz_name))
    if crosses_midnight:
        now = now + timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def _configured_length(raw: str) -> BriefingLength:
    """settings.briefing_length is a plain string; map it, and fall back
    loudly rather than crashing the only scheduled run of the day."""
    try:
        return BriefingLength(raw)
    except ValueError:
        log.error(
            "invalid BRIEFING_LENGTH %r; using %s",
            raw, BriefingLength.STANDARD.value,
        )
        return BriefingLength.STANDARD


def _build_live_providers() -> tuple[LLMProvider, TTSProvider]:
    """Real providers if this machine is configured for them, fixture/fake
    providers otherwise — matching app.api.briefing's own decision so a
    scheduled run and a manual POST /briefing/generate agree on what
    "configured" means. Imported lazily, same reason app.api.briefing does:
    a machine with zero credentials must still be able to start the app and
    its (disabled-by-default) scheduler without an import error.
    """
    from app.api.briefing import _credentials_configured
    from app.providers.fake import FakeLLMProvider
    from app.providers.fake_tts import FakeTTSProvider

    if _credentials_configured():
        from app.providers.gemini import GeminiProvider
        from app.providers.google_tts import GoogleTTSProvider

        return GeminiProvider(), GoogleTTSProvider()
    return FakeLLMProvider(), FakeTTSProvider()


async def _send_push(push_provider: PushProvider | None, briefing_id: str) -> None:
    from app.api.devices import list_registered_tokens

    tokens = list_registered_tokens()
    if not tokens:
        log.info("no registered devices; skipping push for %s", briefing_id)
        return

    if push_provider is None:
        try:
            from app.notifications.apns import APNsProvider

            push_provider = APNsProvider()
        except APNsConfigurationError as exc:
            log.warning(
                "APNs not configured, skipping push for %s: %s", briefing_id, exc
            )
            return

    from app.api.devices import unregister_token

    for token in tokens:
        try:
            accepted = await push_provider.send_silent(
                token, {"briefing_id": briefing_id}
            )
            log.info(
                "push for %s: %s", briefing_id, "accepted" if accepted else "rejected"
            )
        except TokenUnregisteredError as exc:
            # Apple says this token is permanently dead. Drop it, or the
            # store grows across every reinstall and each morning makes a
            # doomed call whose "rejected" line hides real failures later.
            log.info("pruning unregistered device token: %s", exc)
            unregister_token(token)
        except Exception:
            # A push failure must never take down the job that already
            # produced a real briefing.
            log.exception("push failed for briefing %s", briefing_id)


async def run_daily_briefing_job(
    *,
    llm: LLMProvider | None = None,
    tts: TTSProvider | None = None,
    push_provider: PushProvider | None = None,
    storage: BriefingStorage | None = None,
) -> None:
    """The scheduled job body. Also callable directly (tests, a manual
    trigger) with fakes injected for every dependency.

    Requirements this satisfies:
    - idempotent: if today's briefing already exists, this is a no-op —
      the job may fire more than once (misfire replay, manual trigger,
      overlapping process restarts).
    - a failed generation is logged and swallowed here so it cannot escape
      into APScheduler and disable the job (APScheduler already isolates
      job execution per-run, but a job that raises still gets logged as an
      error event; this keeps that error message meaningful instead of a
      bare traceback from three layers of async machinery).
    """
    settings = get_settings()
    storage = storage or LocalFilesystemStorage()
    _, _, crosses_midnight = _compute_fire_time(
        settings.wake_time, settings.generation_lead_minutes
    )
    briefing_id = _target_briefing_id(
        settings.user_timezone, crosses_midnight=crosses_midnight
    )

    if storage.load_manifest(briefing_id) is not None:
        log.info(
            "briefing %s already exists; scheduled run is a no-op", briefing_id
        )
        return

    if llm is None or tts is None:
        built_llm, built_tts = _build_live_providers()
        llm = llm if llm is not None else built_llm
        tts = tts if tts is not None else built_tts

    try:
        # Honour the configured length. Without this the cron always took
        # the STANDARD default, so BRIEFING_LENGTH=full produced a 15-minute
        # briefing on demand and a 7-minute one every morning — and the
        # morning one is the whole product.
        await generate_briefing(
            briefing_id=briefing_id,
            llm=llm,
            tts=tts,
            storage=storage,
            length=_configured_length(settings.briefing_length),
        )
    except Exception:
        log.exception(
            "scheduled briefing generation for %s failed; scheduler keeps running",
            briefing_id,
        )
        return

    log.info("scheduled briefing generation for %s complete", briefing_id)
    await _send_push(push_provider, briefing_id)


def start_scheduler(app: FastAPI) -> AsyncIOScheduler | None:
    """Wire the daily job into an already-running asyncio event loop.

    Returns None (and starts nothing) when `settings.scheduler_enabled` is
    False — the default, so the test suite and local dev never schedule
    real generation just by importing this module or starting the app.
    """
    global _scheduler
    settings = get_settings()

    if not settings.scheduler_enabled:
        log.info("scheduler disabled (SCHEDULER_ENABLED=false); not starting")
        return None

    hour, minute, _crosses_midnight = _compute_fire_time(
        settings.wake_time, settings.generation_lead_minutes
    )
    trigger = CronTrigger(
        hour=hour, minute=minute, timezone=_user_tz(settings.user_timezone)
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_daily_briefing_job,
        trigger=trigger,
        id=JOB_ID,
        replace_existing=True,
        # A laptop asleep at 05:00 and woken at 09:00 must run the job ONCE,
        # not once per missed 24h-cron-tick — coalesce collapses any queued
        # missed fires into a single run, and misfire_grace_time bounds how
        # late a fire is still allowed to run at all (past that, it's
        # skipped rather than queued for a stale "now").
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    log.info(
        "scheduler started: job %r daily at %02d:%02d %s (wake_time=%s minus "
        "%d min lead)",
        JOB_ID, hour, minute, settings.user_timezone,
        settings.wake_time, settings.generation_lead_minutes,
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        log.info("scheduler stopped")
        _scheduler = None


def get_scheduler() -> AsyncIOScheduler | None:
    """For tests/introspection — the running scheduler, or None if disabled."""
    return _scheduler
