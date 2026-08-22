"""
Device token registration.

    POST   /devices/register     register (or refresh) an APNs device token
    DELETE /devices/{token}      unregister a device token

This handles a per-device push credential, so it is guarded with the same
`require_api_token` dependency as the briefing routes — CLAUDE.md's rule
that any route serving or accepting personal data must fail closed, not
default open. That is not a hypothetical here: an unauthenticated route
publishing personal data (a full briefing manifest, over a public tunnel) is
exactly what happened earlier in this project. A device token is a lower
sensitivity payload than an inbox, but the same rule applies rather than
re-litigating "is this one sensitive enough" per route.

Storage: a flat JSON file under `settings.audio_output_dir`. This is
intentionally the simplest thing that works for one user and a handful of
devices — NOT the long-term store. A real multi-user deployment wants this
in the SQLAlchemy database already wired up (see app/core/config.py
`database_url`), keyed by user, with expiry handling for tokens Apple
reports as stale via the feedback service.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_api_token
from app.core.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_api_token)])

_lock = Lock()


def _truncate_token(token: str) -> str:
    """Never log a device token in full — it is a per-device credential."""
    if len(token) <= 8:
        return "***"
    return f"{token[:4]}…{token[-4:]}"


def _store_path() -> Path:
    base = Path(get_settings().audio_output_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / "device_tokens.json"


def _load_tokens() -> list[str]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        log.warning("device token store at %s is unreadable; treating as empty", path)
        return []
    if not isinstance(data, list):
        return []
    return [t for t in data if isinstance(t, str)]


def _save_tokens(tokens: list[str]) -> None:
    path = _store_path()
    path.write_text(json.dumps(sorted(set(tokens)), indent=2))


def register_token(token: str) -> None:
    """Used by the scheduler to read tokens too; kept here so there is one
    place that knows the storage format."""
    with _lock:
        tokens = _load_tokens()
        if token not in tokens:
            tokens.append(token)
        _save_tokens(tokens)


def unregister_token(token: str) -> bool:
    with _lock:
        tokens = _load_tokens()
        if token not in tokens:
            return False
        tokens.remove(token)
        _save_tokens(tokens)
        return True


def list_registered_tokens() -> list[str]:
    with _lock:
        return _load_tokens()


class RegisterRequest(BaseModel):
    device_token: str


class RegisterResponse(BaseModel):
    status: str


@router.post("/register", response_model=RegisterResponse)
async def register(request: RegisterRequest) -> RegisterResponse:
    token = request.device_token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="device_token must not be empty")
    # The store does blocking file IO under a threading.Lock. Negligible for
    # one user, but it shares an event loop with a ~70s generation and audio
    # downloads, so keep it off the loop rather than coupling them.
    await asyncio.to_thread(register_token, token)
    log.info("registered device token %s", _truncate_token(token))
    return RegisterResponse(status="ok")


@router.delete("/{token}", response_model=RegisterResponse)
async def unregister(token: str) -> RegisterResponse:
    found = await asyncio.to_thread(unregister_token, token)
    if not found:
        raise HTTPException(status_code=404, detail="device token not registered")
    log.info("unregistered device token %s", _truncate_token(token))
    return RegisterResponse(status="ok")
