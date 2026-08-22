"""Mercury backend entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import briefing, devices
from app.core.config import get_settings
from app.scheduler.cron import shutdown_scheduler, start_scheduler

settings = get_settings()
logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # start_scheduler no-ops (returns None) when SCHEDULER_ENABLED is unset
    # or false, which is the default — the test suite and local dev must
    # not schedule real generation just by starting the app.
    start_scheduler(app)
    try:
        yield
    finally:
        shutdown_scheduler()


app = FastAPI(
    title="Mercury",
    description="Daily audio briefing generation pipeline",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "mercury",
        # The client derives briefing ids locally (the background task has no
        # channel to be told one), so it needs the timezone the SERVER names
        # them in. Without this the two disagree the moment the device leaves
        # the configured zone and every prefetch 404s.
        "timezone": get_settings().user_timezone,
    }


# Routers get mounted here as they land.
app.include_router(briefing.router, prefix="/briefing", tags=["briefing"])
app.include_router(devices.router, prefix="/devices", tags=["devices"])
# from app.api import auth, voice
# app.include_router(auth.router,  prefix="/auth")
# app.include_router(voice.router, prefix="/voice")
