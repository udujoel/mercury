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
from urllib.parse import urljoin, urlsplit, urlunsplit

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


def resolve_public_url(url: str) -> tuple[str, str]:
    """Validate `url` and return (validated_ip, hostname).

    The IP is returned so the caller can CONNECT to the address that was
    actually checked. Validating a hostname and then letting the HTTP client
    resolve it again is a DNS-rebinding hole: a host under the attacker's
    control can answer with a public address for our check and a loopback or
    metadata address microseconds later for the connection.
    """
    parts = urlsplit(url)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise BlockedURLError(f"scheme {parts.scheme!r} not allowed")
    if not parts.hostname:
        raise BlockedURLError("URL has no host")

    addresses = _addresses_for(parts.hostname)
    for address in addresses:
        # is_global is False for loopback, link-local (169.254.169.254 —
        # cloud instance metadata), private RFC1918 ranges, multicast,
        # reserved and unspecified addresses. One check, no allowlist to
        # keep in sync. EVERY address must pass: a name with both a public
        # and a loopback record must not slip through.
        if not address.is_global:
            raise BlockedURLError(
                f"{parts.hostname} resolves to non-public address {address}"
            )
    return str(addresses[0]), parts.hostname


def assert_public_url(url: str) -> None:
    """Raise BlockedURLError unless `url` is a public http(s) destination."""
    resolve_public_url(url)


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> httpx.Response:
    """GET `url`, validating every hop, pinning DNS, and capping the body.

    Three things this does that a plain `client.get(..., follow_redirects=True)`
    does not:

    1. Validates each redirect hop before requesting it. A URL that passes
       validation can 302 to one that would not, and httpx runs the whole
       chain before returning.
    2. Connects to the IP that was validated, carrying the original hostname
       in the Host header and in TLS SNI. Re-resolving at connect time would
       leave a DNS-rebinding window.
    3. Streams the body and stops at the cap. Reading `response.content` after
       the fact is too late — the bytes are already buffered, so a response
       with no Content-Length (or a dishonest one) defeats the limit.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        ip, hostname = resolve_public_url(current)
        parts = urlsplit(current)

        # Swap the host for the validated literal; keep everything else.
        literal = f"[{ip}]" if ":" in ip else ip
        netloc = f"{literal}:{parts.port}" if parts.port else literal
        pinned = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))

        request_headers = dict(headers or {})
        # Virtual hosts and TLS both need the real name, not the IP.
        request_headers["Host"] = f"{hostname}:{parts.port}" if parts.port else hostname

        async with client.stream(
            "GET",
            pinned,
            timeout=timeout,
            headers=request_headers,
            follow_redirects=False,
            extensions={"sni_hostname": hostname},
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise BlockedURLError(f"redirect from {current} without Location")
                # Resolve relative redirects against the ORIGINAL url, not the
                # IP-pinned one, so the next hop keeps the real hostname.
                current = urljoin(current, location)
                log.debug("following redirect to %s", current)
                continue

            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise BlockedURLError(
                    f"response from {current} declares {declared} bytes "
                    f"(cap {max_bytes})"
                )

            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    # Stop pulling bytes the moment the cap is passed, rather
                    # than discovering it after the whole thing is in memory.
                    raise BlockedURLError(
                        f"response from {current} exceeded {max_bytes} bytes"
                    )

            # aiter_bytes() yields DECODED bytes, so the reconstructed
            # response must not keep headers describing the encoded form —
            # Content-Encoding would make httpx try to gunzip already-plain
            # bytes, and Content-Length would describe the compressed size.
            clean_headers = httpx.Headers(
                [
                    (k, v)
                    for k, v in response.headers.multi_items()
                    if k.lower() not in ("content-encoding", "content-length")
                ]
            )
            return httpx.Response(
                status_code=response.status_code,
                headers=clean_headers,
                content=bytes(body),
                request=response.request,
            )

    raise BlockedURLError(f"too many redirects starting at {url}")
