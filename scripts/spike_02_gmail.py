#!/usr/bin/env python3
"""
Phase 0 Spike 2 — Gmail fetch.

Lists yesterday's messages, extracts plain text bodies (walking MIME trees,
falling back to HTML-stripped text), and prints subjects with sender names.
Also runs the newsletter and requires_response/priority heuristics from
PRD F-04/F-05 and emits normalised `Item` objects.

This is throwaway spike code (see CLAUDE.md, docs/ROADMAP.md Phase 0). It is
written so the MIME-walking and heuristic logic is easy to lift into
backend/app/ingestion/gmail.py during Phase 1 — but that module does not
exist yet and this script must not import from backend/app.

Usage:
    python scripts/spike_02_gmail.py --fixture [--json]
    python scripts/spike_02_gmail.py --live [--json]

--fixture is the default whenever credentials.json is not present at the
repo root, so this script always has a working offline demo.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from _google_auth import credentials_available, get_credentials  # noqa: E402
from app.core.schemas import Item, Priority, SourceType  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "backend/tests/fixtures/gmail_messages.json"

GMAIL_QUERY = "newer_than:1d -category:promotions -category:social"
MAX_MESSAGES = 60

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

QUOTE_HEADER_RE = re.compile(
    r"^\s*On .+ wrote:\s*$", re.MULTILINE | re.IGNORECASE
)
TRACKING_PIXEL_RE = re.compile(
    r"<img[^>]+(width=[\"']?1[\"']?|height=[\"']?1[\"']?)[^>]*>", re.IGNORECASE
)
UNSUBSCRIBE_LINE_RE = re.compile(
    r"^.*unsubscribe.*$", re.IGNORECASE | re.MULTILINE
)


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
    """Priority/requires_response heuristic.

    Reasoning (tune this — it is a taste call, not a fact): a newsletter is
    never urgent regardless of content, so it is always low priority and
    never requires a response. Otherwise, a message that puts the user in
    the direct To line (not just Cc/Bcc) AND either asks a question or uses
    an ask-phrasing ("can you...", "let me know...") is the strongest signal
    someone is waiting on a reply from the user specifically, so it is high
    priority + requires_response. Everything else defaults to medium: it is
    real correspondence but nothing in the text signals an outstanding ask.
    """
    if is_newsletter:
        return Priority.LOW, False

    user_in_to = any(user_email.lower() in addr.lower() for addr in to_addresses)
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


# ──────────────────────────────────────────────────────────────
# Fetch paths
# ──────────────────────────────────────────────────────────────


def load_fixture_messages() -> list[dict[str, Any]]:
    data = json.loads(FIXTURE_PATH.read_text())
    return data["messages"]


def fetch_live_messages() -> list[dict[str, Any]]:
    from googleapiclient.discovery import build

    creds = get_credentials()
    service = build("gmail", "v1", credentials=creds)

    resp = (
        service.users()
        .messages()
        .list(userId="me", q=GMAIL_QUERY, maxResults=MAX_MESSAGES)
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


# ──────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────


def print_table(items: list[Item]) -> None:
    header = f"{'PRIORITY':<8} {'NEWSLETTER':<11} {'CHARS':>6}  {'SENDER':<28} SUBJECT"
    print(header)
    print("-" * len(header))
    for item in items:
        is_nl = "yes" if item.raw.get("is_newsletter") else "no"
        print(
            f"{item.priority.value:<8} {is_nl:<11} {len(item.body):>6}  "
            f"{item.source_name[:28]:<28} {item.title}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike 2 — Gmail fetch")
    parser.add_argument("--fixture", action="store_true", help="use offline fixture")
    parser.add_argument("--live", action="store_true", help="hit the real Gmail API")
    parser.add_argument("--json", action="store_true", help="dump normalised Items")
    parser.add_argument("--user-email", default="udujoel@gmail.com")
    args = parser.parse_args()

    use_live = args.live and not args.fixture
    if not args.live and not args.fixture:
        use_live = credentials_available()

    if use_live:
        print(f"[live] fetching with query: {GMAIL_QUERY!r}", file=sys.stderr)
        try:
            raw_messages = fetch_live_messages()
        except Exception as exc:
            print(f"live fetch failed: {exc}", file=sys.stderr)
            return 1
    else:
        print(f"[fixture] reading {FIXTURE_PATH}", file=sys.stderr)
        raw_messages = load_fixture_messages()

    items: list[Item] = []
    skipped = 0
    for raw in raw_messages:
        try:
            items.append(message_to_item(raw, args.user_email))
        except Exception as exc:
            skipped += 1
            print(
                f"skipping malformed message {raw.get('id', '?')}: {exc}",
                file=sys.stderr,
            )

    if args.json:
        print(json.dumps([json.loads(i.model_dump_json()) for i in items], indent=2))
    else:
        print_table(items)
        print(f"\n{len(items)} messages parsed, {skipped} skipped as malformed.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
