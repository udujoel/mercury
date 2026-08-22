"""
Generation job tracking.

`POST /briefing/generate` used to run the whole pipeline inside the request
and return the finished manifest. That works on localhost and fails through
any proxy: a full live run takes minutes, and Cloudflare's tunnel enforces a
hard 120-second read timeout that cannot be raised, so the phone got

    Error 524: origin_response_timeout

after waiting two minutes — while the server carried on generating a briefing
nobody was listening for any more.

ADR-001 already says the phone should never wait on generation: the server
generates, the phone plays. Holding an HTTP connection open for the duration
was a shortcut from the Phase 1 vertical slice that survived longer than it
should have. Generation now runs as a background job and the client polls,
which removes the timeout class of failure entirely rather than raising a
limit until it breaks somewhere else.

Deliberately in-process and in-memory: one user, one machine, and a job that
does not survive a restart is fine because the pipeline is idempotent — the
scheduler and the API both skip a briefing that already exists in storage.
A multi-user deployment wants this in the database that config.py already
points at.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

log = logging.getLogger(__name__)


class JobState(str, Enum):
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


@dataclass
class Job:
    briefing_id: str
    state: JobState = JobState.GENERATING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    error: str | None = None
    task: asyncio.Task | None = None

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at or datetime.now(UTC)
        return (end - self.started_at).total_seconds()


_jobs: dict[str, Job] = {}


def get(briefing_id: str) -> Job | None:
    return _jobs.get(briefing_id)


def is_running(briefing_id: str) -> bool:
    job = _jobs.get(briefing_id)
    return job is not None and job.state is JobState.GENERATING


def start(briefing_id: str, coro) -> Job:
    """Register and launch a generation job.

    If one is already running for this briefing id, the existing job is
    returned and the coroutine is closed — a second tap on the greeting card
    must not start a second expensive generation.
    """
    existing = _jobs.get(briefing_id)
    if existing is not None and existing.state is JobState.GENERATING:
        log.info("generation already running for %s; reusing job", briefing_id)
        coro.close()
        return existing

    job = Job(briefing_id=briefing_id)
    _jobs[briefing_id] = job

    async def _run() -> None:
        try:
            await coro
            job.state = JobState.READY
            log.info("generation for %s finished in %.0fs", briefing_id, job.elapsed_s)
        except Exception as exc:
            job.state = JobState.FAILED
            job.error = f"{type(exc).__name__}: {exc}"
            log.exception("generation for %s failed", briefing_id)
        finally:
            job.finished_at = datetime.now(UTC)

    job.task = asyncio.create_task(_run())
    return job


def clear() -> None:
    """Test hook — drop all tracked jobs."""
    _jobs.clear()
