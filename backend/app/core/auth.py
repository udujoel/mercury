"""
API authentication.

The briefing endpoints return the user's inbox and calendar rendered as
speech. There is no tier of this data that is safe to serve anonymously, and
the moment the backend is reachable from anywhere but localhost — a
cloudflared tunnel for device testing is enough — "unauthenticated" means
"published".

That is not hypothetical. During Phase 2 testing a tunnel was opened so a
physical iPhone could reach the backend, and the full manifest (44 turns, 66
source refs, built from a real inbox) was fetchable by anyone holding the
URL. `trycloudflare.com` hostnames are not secret; they leak through logs,
referrers and certificate transparency.

So: **fail closed**. If MERCURY_API_TOKEN is unset the briefing routes refuse
to serve at all, rather than defaulting to open and relying on the deployment
never being exposed. A missing token is a misconfiguration, and the safe
behaviour for a misconfiguration is to stop.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from fastapi import Header, HTTPException, status

from app.core.config import get_settings

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Mercury-Token"
MIN_TOKEN_LENGTH = 32


def generate_token() -> str:
    """A token suitable for MERCURY_API_TOKEN."""
    return secrets.token_urlsafe(32)


async def require_api_token(
    x_mercury_token: str | None = Header(default=None),
) -> None:
    """FastAPI dependency guarding every route that exposes personal data."""
    settings = get_settings()
    expected = settings.mercury_api_token

    if not expected:
        log.error(
            "MERCURY_API_TOKEN is not set; refusing to serve briefing data. "
            'Generate one with: python -c "import secrets; '
            'print(secrets.token_urlsafe(32))" and set it in backend/.env'
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Server is not configured for authenticated access. Set "
                "MERCURY_API_TOKEN in backend/.env and restart. Refusing to "
                "serve personal data without it."
            ),
        )

    if len(expected) < MIN_TOKEN_LENGTH:
        # A short token is brute-forceable over a public tunnel, and a
        # placeholder like "changeme" is worse than none because it reads as
        # configured.
        log.error("MERCURY_API_TOKEN is shorter than %d characters", MIN_TOKEN_LENGTH)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"MERCURY_API_TOKEN must be at least {MIN_TOKEN_LENGTH} "
                "characters. Refusing to serve personal data behind a weak "
                "token."
            ),
        )

    # compare_digest, not ==: a plain comparison short-circuits on the first
    # differing byte and leaks the token prefix through response timing.
    if not x_mercury_token or not hmac.compare_digest(x_mercury_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {TOKEN_HEADER}.",
        )
