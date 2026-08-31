"""
Gmail ingestion.

Ports the MIME-walking, base64url decoding, quote/signature stripping and
newsletter-detection heuristics proven in `scripts/spike_02_gmail.py` into a
proper pipeline module. See that file's docstring for the reasoning behind
each heuristic — this module lifts the logic verbatim rather than
reinventing it.

CLAUDE.md rule 4: read-only scopes only (gmail.readonly). This module never
sends, deletes, labels or modifies anything — it only lists and reads.

CLAUDE.md rule 5: raw email bodies are ephemeral. This module holds message
bodies only in memory for the duration of a single `fetch_emails` call and
returns them embedded in `Item.body` for the generation run; nothing here
writes a raw body to disk.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.schemas import Item, Priority, SourceType

log = logging.getLogger(__name__)

GMAIL_QUERY = "newer_than:1d -category:promotions -category:social"

# app/core/config.py has no dedicated "user email" setting (user_name is a
# display name, e.g. "Joel"), and the Gmail account address is whatever

# ──────────────────────────────────────────────────────────────
# MIME walking
# ──────────────────────────────────────────────────────────────


def decode_base64url(data: str) -> str:
    """Gmail API base64url bodies commonly arrive without padding, and can
    use either '-'/'_' or occasionally the standard alphabet. Pad and decode
    defensively; never let a bad body kill the whole message."""
    if not data:
        return ""
    data = data.replace("-", "+").replace("_", "/")
    padding = (-len(data)) % 4
    data += "=" * padding
    try:
        return base64.b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def walk_mime_parts(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Recursively descend payload/parts and return (plain_text, html_text),
    preferring the first text/plain and text/html leaves found. Skips parts
    that are attachments (have a filename) or lack inline body data."""
    plain: str | None = None
    html: str | None = None

    def visit(node: dict[str, Any]) -> None:
        nonlocal plain, html
        mime_type = node.get("mimeType", "")
        filename = node.get("filename") or ""
        body = node.get("body", {})
        data = body.get("data")

        if mime_type == "text/plain" and not filename and data and plain is None:
            plain = decode_base64url(data)
        elif mime_type == "text/html" and not filename and data and html is None:
            html = decode_base64url(data)

        for part in node.get("parts") or []:
            visit(part)

    visit(payload)
    return plain, html


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # tracking pixels: 1x1 (or width/height <=2) images carry no content
    for img in soup.find_all("img"):
        width, height = img.get("width"), img.get("height")
        try:
            if width and height and int(width) <= 2 and int(height) <= 2:
                img.decompose()
        except (TypeError, ValueError):
            pass
    return soup.get_text(separator="\n")


def extract_body(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to HTML stripped with BeautifulSoup."""
    plain, html = walk_mime_parts(payload)
    if plain:
        return plain
    if html:
        return html_to_text(html)
    return ""


# ──────────────────────────────────────────────────────────────
# Cleaning
# ──────────────────────────────────────────────────────────────

QUOTE_HEADER_RE = re.compile(r"^\s*On .+ wrote:\s*$", re.MULTILINE | re.IGNORECASE)
TRACKING_PIXEL_RE = re.compile(
    r"<img[^>]+(width=[\"']?1[\"']?|height=[\"']?1[\"']?)[^>]*>", re.IGNORECASE
)
UNSUBSCRIBE_LINE_RE = re.compile(r"^.*unsubscribe.*$", re.IGNORECASE | re.MULTILINE)


def strip_quote_chain(text: str) -> str:
    """Cut everything from the first 'On ... wrote:' line onward, and drop
    any leading '>' quoted lines that remain."""
    match = QUOTE_HEADER_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = [line for line in text.split("\n") if not line.lstrip().startswith(">")]
    return "\n".join(lines)


def strip_signature(text: str) -> str:
    """Cut everything from a standalone '-- ' signature delimiter onward."""
    marker = "\n-- \n"
    idx = text.find(marker)
    if idx == -1 and text.startswith("-- \n"):
        idx = 0
    if idx != -1:
        text = text[:idx]
    return text


def strip_unsubscribe_footer(text: str) -> str:
    return UNSUBSCRIBE_LINE_RE.sub("", text)


def collapse_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_body(raw_html_or_text: str) -> str:
    text = TRACKING_PIXEL_RE.sub("", raw_html_or_text)
    text = strip_quote_chain(text)
    text = strip_signature(text)
    text = strip_unsubscribe_footer(text)
    text = collapse_whitespace(text)
    return text


# ──────────────────────────────────────────────────────────────
# Newsletter detection (PRD F-04)
# ──────────────────────────────────────────────────────────────

NEWSLETTER_SENDER_PATTERNS = [
    "noreply@",
    "no-reply@",
    "newsletter@",
    "newsletters@",
    "hello@",
    "@substack.com",
    "@mailchimp.com",
    "@sendgrid.net",
]


def detect_newsletter(headers: dict[str, str]) -> tuple[bool, str | None]:
    """Returns (is_newsletter, signal) where signal names which heuristic
    fired, so the classification is debuggable rather than a black box."""
    if headers.get("List-Unsubscribe"):
        return True, "List-Unsubscribe header"
    if headers.get("Precedence", "").lower() == "bulk":
        return True, "Precedence: bulk"
    if headers.get("List-Id"):
        return True, "List-Id header"

    sender = headers.get("From", "").lower()
    for pattern in NEWSLETTER_SENDER_PATTERNS:
        if pattern in sender:
            return True, f"sender heuristic ({pattern})"

    return False, None


# ──────────────────────────────────────────────────────────────
# requires_response / priority (PRD F-05)
# ──────────────────────────────────────────────────────────────

QUESTION_PHRASES = [
    "can you",
    "could you",
    "would you",
    "let me know",
    "please review",
    "please confirm",
    "any thoughts",
    "what do you think",
]


def classify_priority(
    *,
    is_newsletter: bool,
    body: str,
    to_addresses: list[str],
    user_email: str,
) -> tuple[Priority, bool]:
    """Priority/requires_response heuristic. See spike_02_gmail.py for the
    full reasoning — lifted verbatim."""
    if is_newsletter:
        return Priority.LOW, False

    # Guard the empty case: "" is a substring of every address, so an
    # unset USER_EMAIL would make every message look directly addressed.
    user_in_to = bool(user_email) and any(
        user_email.lower() in addr.lower() for addr in to_addresses
    )
    has_question = "?" in body
    has_ask_phrase = any(phrase in body.lower() for phrase in QUESTION_PHRASES)

    if user_in_to and (has_question or has_ask_phrase):
        return Priority.HIGH, True

    return Priority.MEDIUM, False


# ──────────────────────────────────────────────────────────────
# Normalisation
# ──────────────────────────────────────────────────────────────


def header_dict(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"]: h["value"] for h in payload.get("headers", [])}


def parse_sender(from_header: str) -> tuple[str, str]:
    """'Signals Weekly <newsletter@signalsweekly.com>' -> (name, email)."""
    match = re.match(r"^(.*?)\s*<(.+?)>\s*$", from_header.strip())
    if match:
        name = match.group(1).strip().strip('"')
        return (name or match.group(2)), match.group(2)
    return from_header.strip(), from_header.strip()


def _parse_date(date_str: str | None, internal_date_ms: str | None) -> datetime:
    if internal_date_ms:
        try:
            return datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=UTC)
        except (ValueError, TypeError):
            pass
    if date_str:
        try:
            from email.utils import parsedate_to_datetime

            return parsedate_to_datetime(date_str)
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC)


def message_to_item(raw: dict[str, Any], user_email: str) -> Item:
    payload = raw.get("payload")
    if payload is None:
        raise ValueError(f"message {raw.get('id')} has no payload")

    headers = header_dict(payload)
    subject = headers.get("Subject", "(no subject)")
    from_header = headers.get("From", "unknown@unknown")
    sender_name, sender_email = parse_sender(from_header)
    to_addresses = [a.strip() for a in headers.get("To", "").split(",") if a.strip()]

    body_raw = extract_body(payload)
    body = clean_body(body_raw)

    is_newsletter, signal = detect_newsletter(headers)
    priority, requires_response = classify_priority(
        is_newsletter=is_newsletter,
        body=body,
        to_addresses=to_addresses,
        user_email=user_email,
    )

    date_str = headers.get("Date")
    timestamp = _parse_date(date_str, raw.get("internalDate"))

    return Item(
        source_type=SourceType.EMAIL,
        source_name=sender_name,
        title=subject,
        body=body,
        author=sender_email,
        timestamp=timestamp,
        requires_response=requires_response,
        priority=priority,
        raw={
            "message_id": raw.get("id"),
            "thread_id": raw.get("threadId"),
            "is_newsletter": is_newsletter,
            "newsletter_signal": signal,
            "to": to_addresses,
        },
    )


# ──────────────────────────────────────────────────────────────
# Fetch paths
# ──────────────────────────────────────────────────────────────


def _load_fixture_messages(fixture: Path) -> list[dict[str, Any]]:
    data = json.loads(fixture.read_text())
    return data["messages"]


def _fetch_live_messages(max_messages: int) -> list[dict[str, Any]]:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "google-api-python-client is not installed; cannot fetch Gmail "
            "live. Install it or pass fixture= for the offline path."
        ) from exc

    # Reuse the Phase 0 OAuth helper — same scopes, same token cache.
    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from _google_auth import get_credentials

    # Deliberately NOT re-wrapped in a plain RuntimeError. MissingCredentialsError
    # already is one, so the documented contract holds, and flattening it threw
    # away the ExpiredCredentialsError subtype — the single most likely
    # production failure here, and the only one with a known permanent fix.
    # app.cli keys its loudest error off that type, and it cannot do that if
    # the type is discarded one frame after it is raised.
    creds = get_credentials()

    service = build("gmail", "v1", credentials=creds)

    resp = (
        service.users()
        .messages()
        .list(userId="me", q=GMAIL_QUERY, maxResults=max_messages)
        .execute()
    )
    message_refs = resp.get("messages", [])

    full_messages = []
    for ref in message_refs:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        full_messages.append(msg)
    return full_messages


async def fetch_emails(
    *, fixture: Path | None = None, max_messages: int = 60
) -> list[Item]:
    """Fetch and normalise recent Gmail messages into `Item`s.

    fixture=<path> reads that JSON file instead of hitting the network.
    fixture=None hits the live Gmail API and raises a clear, actionable
    RuntimeError naming the exact missing credential if it cannot run.
    """
    user_email = get_settings().user_email
    if not user_email:
        # Without it, "am I a direct To recipient" cannot be evaluated and
        # nothing is ever scored HIGH, so the briefing quietly loses its
        # "needs a response" calls. Warn rather than degrade silently.
        log.warning(
            "USER_EMAIL is unset; no email can be scored HIGH priority. "
            "Set USER_EMAIL in backend/.env to the Google account the "
            "OAuth token belongs to."
        )

    if fixture is not None:
        raw_messages = _load_fixture_messages(fixture)
    else:
        raw_messages = _fetch_live_messages(max_messages)

    items: list[Item] = []
    for raw in raw_messages[:max_messages]:
        try:
            items.append(message_to_item(raw, user_email))
        except Exception:
            # A malformed message must never take down the whole run
            # (CLAUDE.md rule 7 — graceful degradation).
            continue
    return items
