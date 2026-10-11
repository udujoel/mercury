"""
APNs silent push provider.

IMPORTANT — read this before assuming a missing push is a bug: a silent
push (`content-available: 1`, `apns-push-type: background`) is a *hint* to
iOS that new content is ready, not a delivery mechanism. iOS throttles and
drops silent pushes freely — based on battery, Low Power Mode, how often the
app is used, how many silent pushes it has already received that day, and
signals Apple does not document. A silent push can be accepted with a 200 by
APNs and never reach the device. That is expected, not a failure of this
module. This is exactly why ADR-001's three-layer strategy (PRIMARY cron +
push hint, SECONDARY BGProcessingTask prefetch, FALLBACK foreground fetch on
launch) does not treat the push as load-bearing: the foreground fetch on
launch is what actually guarantees the user gets the new briefing.

Auth: a provider token (JWT, ES256) signed with the `.p8` key from the Apple
Developer portal, per Apple's "token-based connection to APNs" scheme. One
token is reused across requests (Apple asks that you not mint a new one per
push) and is cheap to regenerate, so this module just signs a fresh one per
`APNsProvider` construction rather than adding cache/refresh complexity for
what is, at personal scale, a handful of pushes a day.

Behind `PushProvider` (app/core/providers.py style) so the pipeline never
imports `APNsProvider` directly — swapping to a different push service, or
to `FakePushProvider` in tests, is a one-line change at the call site.
"""

from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

import httpx
import jwt

from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)

APNS_PRODUCTION_HOST = "https://api.push.apple.com"
APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"


def _truncate_token(device_token: str) -> str:
    """Never log a device token in full — it is a per-device credential."""
    if len(device_token) <= 8:
        return "***"
    return f"{device_token[:4]}…{device_token[-4:]}"


@runtime_checkable
class PushProvider(Protocol):
    async def send_silent(self, device_token: str, payload: dict) -> bool:
        """Send a silent (background) push. Returns whether APNs *accepted*
        the push for delivery — not whether the device received it; see the
        module docstring."""
        ...


class TokenUnregisteredError(RuntimeError):
    """APNs returned 410 Unregistered: this device token is permanently dead
    (the app was deleted, or the token was reissued). Apple's contract is to
    STOP sending to it, so callers should unregister rather than retry it
    every morning forever."""


class APNsConfigurationError(RuntimeError):
    """Raised when APNs config is missing. Never send a fake/no-op push in
    this case — callers must be able to tell "not configured" apart from
    "sent"."""


def _require_config(settings: Settings) -> tuple[str, str, str, str]:
    missing = []
    if not settings.apns_key_path:
        missing.append("APNS_KEY_PATH")
    if not settings.apns_key_id:
        missing.append("APNS_KEY_ID")
    if not settings.apns_team_id:
        missing.append("APNS_TEAM_ID")
    if not settings.apns_topic:
        missing.append("APNS_TOPIC")
    if missing:
        raise APNsConfigurationError(
            "APNs is not configured — missing: "
            f"{', '.join(missing)}. Set these in backend/.env (see "
            "docs/phase2/automation.md for how to create the .p8 key). "
            "Refusing to fake a send."
        )
    return (
        settings.apns_key_path,
        settings.apns_key_id,
        settings.apns_team_id,
        settings.apns_topic,
    )


class APNsProvider:
    """Real APNs HTTP/2 provider, token-based auth (ES256 JWT over the
    `.p8` key). Satisfies `PushProvider` structurally."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        key_path, key_id, team_id, topic = _require_config(settings)

        try:
            private_key = open(key_path, encoding="utf-8").read()  # noqa: SIM115
        except OSError as exc:
            raise APNsConfigurationError(
                f"APNS_KEY_PATH={key_path!r} could not be read: {exc}. "
                "Refusing to fake a send."
            ) from exc

        self._topic = topic
        self._host = (
            APNS_SANDBOX_HOST if settings.apns_use_sandbox else (APNS_PRODUCTION_HOST)
        )
        self._token = jwt.encode(
            {"iss": team_id, "iat": int(time.time())},
            private_key,
            algorithm="ES256",
            headers={"kid": key_id},
        )

    async def send_silent(self, device_token: str, payload: dict) -> bool:
        body = {"aps": {"content-available": 1, **payload}}
        headers = {
            "authorization": f"bearer {self._token}",
            "apns-push-type": "background",
            "apns-priority": "5",
            "apns-topic": self._topic,
        }
        url = f"{self._host}/3/device/{device_token}"
        truncated = _truncate_token(device_token)
        try:
            async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
                response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("APNs push to %s failed to send: %s", truncated, exc)
            return False

        if response.status_code == 200:
            log.info("APNs accepted push for %s", truncated)
            return True

        if response.status_code == 410:
            # Permanent, not transient. Distinguishing it lets the caller
            # prune the token instead of making a doomed call every day and
            # filling the log with "rejected" noise that will later hide a
            # genuine failure.
            raise TokenUnregisteredError(f"APNs reports {truncated} is unregistered")

        log.warning(
            "APNs rejected push for %s: %d %s",
            truncated,
            response.status_code,
            response.text,
        )
        return False


class FakePushProvider:
    """Offline, in-memory PushProvider for tests. Records every call so a
    test can assert what was sent without a real APNs call."""

    def __init__(self, *, should_succeed: bool = True) -> None:
        self.should_succeed = should_succeed
        self.calls: list[tuple[str, dict]] = []

    async def send_silent(self, device_token: str, payload: dict) -> bool:
        self.calls.append((device_token, payload))
        return self.should_succeed
