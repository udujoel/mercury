"""Mercury backend entrypoint."""

import logging

from fastapi import FastAPI

from app.core.config import get_settings

settings = get_settings()
logging.basicConfig(level=settings.log_level)

app = FastAPI(
    title="Mercury",
    description="Daily audio briefing generation pipeline",
    version="0.1.0",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "mercury"}


# Routers get mounted here as they land.
# from app.api import auth, briefing, voice
# app.include_router(auth.router,     prefix="/auth")
# app.include_router(briefing.router, prefix="/briefing")
# app.include_router(voice.router,    prefix="/voice")
