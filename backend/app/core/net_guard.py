"""
Outbound-request guard.

Mercury fetches URLs that strangers control: every RSS entry's `<link>` comes
from a third-party feed, and Phase 4 adds Reddit, Bluesky and Slack on top of
that. Without a guard, "fetch the article" is a server-side request forgery
primitive pointed at our own network.

Two defences, both required:

1. **Destination validation.** Only http/https, and the resolved IP must be a
   public address. Rejecting by hostname alone is not enough — an attacker
   controls DNS for their own domain and can point it at 127.0.0.1 or
   169.254.169.254. We resolve first and check the actual addresses.

2. **Per-hop validation.** Redirects are followed manually, because a URL that
   passes validation can 302 straight to one that would not. httpx's
   `follow_redirects=True` performs the whole chain before we ever see it.

Also caps the response body: httpx has no default maximum, so a feed linking
to a multi-gigabyte file would exhaust memory and take down a run that rule 7
says must survive any single source failing.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_REDIRECTS = 5
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB — far above any real article


class BlockedURLError(Exception):
    """The URL is not a legitimate public destination."""


def _addresses_for(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address `host` resolves to. All of them must be public: a name
    with both a public and a loopback record must not slip through."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise BlockedURLError(f"cannot resolve host {host!r}") from exc
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def assert_public_url(url: str) -> None:
    """Raise BlockedURLError unless `url` is a public http(s) destination."""
    parts = urlsplit(url)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"scheme {parts.scheme!r} not allowed")
    if not parts.hostname:
        raise BlockedURLError("URL has no host")

    for address in _addresses_for(parts.hostname):
        # is_global is False for loopback, link-local (169.254.169.254 —
        # cloud instance metadata), private RFC1918 ranges, multicast,
        # reserved and unspecified addresses. One check, no allowlist to
        # keep in sync.
        if not address.is_global:
            raise BlockedURLError(
                f"{parts.hostname} resolves to non-public address {address}"
            )


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> httpx.Response:
    """GET `url`, validating every hop and capping the body size.

    Redirects are followed by hand so each destination is validated before it
    is requested. Raises BlockedURLError for a disallowed destination and
    httpx errors for transport failures; callers treat both as "this source
    failed" and carry on (rule 7).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        assert_public_url(current)

        response = await client.get(
            current,
            timeout=timeout,
            headers=headers,
            follow_redirects=False,
        )

        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                raise BlockedURLError(f"redirect from {current} without Location")
            nxt = response.next_request
            current = str(nxt.url) if nxt is not None else location
            log.debug("following redirect to %s", current)
            continue

        # Reject on the declared length before reading, when we have one.
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise BlockedURLError(
                f"response from {current} declares {declared} bytes "
                f"(cap {max_bytes})"
            )

        # A missing or lying Content-Length is normal, so enforce the cap
        # against what actually arrives too.
        if len(response.content) > max_bytes:
            raise BlockedURLError(
                f"response from {current} exceeded {max_bytes} bytes"
            )
        return response

    raise BlockedURLError(f"too many redirects starting at {url}")
