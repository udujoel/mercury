"""
Offline tests for app/ingestion/gmail.py, against the shared fixture at
backend/tests/fixtures/gmail_messages.json (owned by another agent — do not
modify it).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.schemas import Priority, SourceType
from app.ingestion import gmail

FIXTURE = Path(__file__).parent / "fixtures" / "gmail_messages.json"


async def test_fetch_emails_offline_returns_items():
    items = await gmail.fetch_emails(fixture=FIXTURE)
    assert items
    for item in items:
        assert item.source_type == SourceType.EMAIL
        assert item.title
        assert isinstance(item.priority, Priority)


async def test_fetch_emails_respects_max_messages():
    items = await gmail.fetch_emails(fixture=FIXTURE, max_messages=1)
    assert len(items) <= 1


async def test_newsletter_is_low_priority_never_requires_response():
    """Narrowed to NON-FINANCIAL newsletters (ROADMAP task 27).

    "Every newsletter is LOW" stopped being what the code claims once a
    bank alert — which carries `List-Unsubscribe` like everything else
    bulk — could be escalated. It stayed green only because this fixture's
    one `noreply@` sender is a flash sale with no urgent phrase in it, so
    it was one fixture edit away from failing for the right reason with a
    misleading message.
    """
    items = await gmail.fetch_emails(fixture=FIXTURE)
    newsletters = [
        i for i in items
        if i.raw.get("is_newsletter") and not i.raw.get("is_urgent")
    ]
    assert newsletters, "fixture should contain at least one newsletter"
    for item in newsletters:
        assert item.priority == Priority.LOW
        assert item.requires_response is False


def test_decode_base64url_handles_missing_padding():
    encoded = "aGVsbG8gd29ybGQ"  # "hello world" without padding
    assert gmail.decode_base64url(encoded) == "hello world"


def test_decode_base64url_handles_bad_input_gracefully():
    assert gmail.decode_base64url("!!!not base64!!!") == "" or isinstance(
        gmail.decode_base64url("!!!not base64!!!"), str
    )


def test_strip_quote_chain_cuts_at_on_wrote():
    text = (
        "Sure, sounds good.\n\n"
        "On Mon, Aug 18, 2026 at 3:00 PM, Bob wrote:\n> old text"
    )
    result = gmail.strip_quote_chain(text)
    assert "old text" not in result
    assert "Sure, sounds good." in result


def test_strip_signature_cuts_at_delimiter():
    text = "Thanks,\nsee you then\n-- \nJoel Udu\nMercury Inc."
    result = gmail.strip_signature(text)
    assert "Mercury Inc." not in result
    assert "see you then" in result


def test_detect_newsletter_list_unsubscribe_header():
    headers = {"List-Unsubscribe": "<mailto:unsub@example.com>", "From": "a@b.com"}
    is_nl, signal = gmail.detect_newsletter(headers)
    assert is_nl is True
    assert signal == "List-Unsubscribe header"


def test_detect_newsletter_regular_sender_is_not_newsletter():
    headers = {"From": "Priya Sharma <priya@acmecorp.com>"}
    is_nl, signal = gmail.detect_newsletter(headers)
    assert is_nl is False
    assert signal is None


def test_classify_priority_high_when_direct_question():
    priority, requires_response = gmail.classify_priority(
        is_newsletter=False,
        body="Can you review this before 9am?",
        to_addresses=["udujoel@gmail.com"],
        user_email="udujoel@gmail.com",
    )
    assert priority == Priority.HIGH
    assert requires_response is True


def test_classify_priority_medium_when_no_ask_signal():
    priority, requires_response = gmail.classify_priority(
        is_newsletter=False,
        body="FYI, the deploy went out last night.",
        to_addresses=["udujoel@gmail.com"],
        user_email="udujoel@gmail.com",
    )
    assert priority == Priority.MEDIUM
    assert requires_response is False


def test_classify_priority_newsletter_always_low():
    priority, requires_response = gmail.classify_priority(
        is_newsletter=True,
        body="Can you believe this deal? Let me know what you think!",
        to_addresses=["udujoel@gmail.com"],
        user_email="udujoel@gmail.com",
    )
    assert priority == Priority.LOW
    assert requires_response is False


async def test_fetch_emails_live_without_fixture_raises_clear_error():
    """No live path can succeed on this machine (no credentials.json) —
    verify the failure names the missing credential rather than crashing
    with an opaque traceback or silently returning nothing."""
    with pytest.raises(Exception) as exc_info:
        await gmail.fetch_emails(fixture=None)
    message = str(exc_info.value)
    assert "credentials" in message.lower() or "google" in message.lower()


def test_malformed_message_is_skipped_not_raised(tmp_path):
    import json

    bad_fixture = tmp_path / "bad_gmail.json"
    bad_fixture.write_text(json.dumps({"messages": [{"id": "broken"}]}))
    import asyncio

    items = asyncio.run(gmail.fetch_emails(fixture=bad_fixture))
    assert items == []
