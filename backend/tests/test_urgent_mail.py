"""Urgent means "this can cost you money", not "this has a question mark".

The 2026-09-14 review found `classify_priority` pinning every bank alert and
account warning to LOW — because `List-Unsubscribe` made them newsletters —
and barring them from the follow-ups segment entirely, while a EUR 3.15 taxi
receipt was read out ahead of a Binance restriction warning.

It also found the obvious wider rule unusable: `requires_response` fires on
"addressed to you, contains a question mark", which a phishing mail satisfies
by construction. A filter that promotes the attacker is worse than none.
"""

from __future__ import annotations

from app.core.schemas import Priority
from app.ingestion.gmail import classify_priority, detect_urgent


def test_words_in_the_middle_of_the_phrase_still_match():
    """The first cut matched "account restricted" and missed "your account
    HAS BEEN restricted", which is how real subject lines are written.
    Found by running it, not by reading it."""
    assert detect_urgent("Your account has been restricted", "")[0]
    assert detect_urgent("Your account was suspended", "")[0]
    assert detect_urgent("Account locked", "")[0]


def test_a_newsletter_reporting_someone_elses_breach_is_not_urgent():
    """The biggest source of false positives, and the expensive direction.

    This segment's whole value is that when it says something needs
    attention, it does. A digest whose body reads "Cloudflare users saw
    unusual sign-in alerts" is reporting news, not addressing the reader.
    """
    assert not detect_urgent(
        "Weekly digest",
        "Cloudflare users saw unusual sign-in alerts this week",
        is_newsletter=True,
    )[0]


def test_an_ordinary_question_is_not_urgent():
    """The rule the review found unusable, asserted against.

    A phishing mail satisfies "addressed to you, contains a question mark" by
    construction, so that heuristic must not reach this segment.
    """
    assert not detect_urgent("Lunch Thursday?", "Can you confirm?")[0]
    assert not detect_urgent("Quick question", "What do you think?")[0]


def test_the_signal_names_what_fired():
    """A classification nobody can argue with is one nobody can fix."""
    urgent, signal = detect_urgent("Payment failed for your subscription", "")
    assert urgent and signal == "payment failed"


def test_a_phrase_deep_in_a_body_is_reporting_not_addressing():
    body = "x" * 2000 + " action required "
    assert not detect_urgent("Some subject", body)[0]


def test_an_urgent_alert_outranks_the_newsletter_floor():
    """The defect itself.

    A bank's restriction warning carries List-Unsubscribe, so it was a
    newsletter, so it was LOW with requires_response=False — sorted below a
    gym mailer by arrival time and structurally unreachable for the one
    segment that tells the listener to act.
    """
    priority, requires_response = classify_priority(
        is_newsletter=True, is_urgent=True,
        body="Your account has been restricted",
        to_addresses=["joel@example.test"], user_email="joel@example.test",
    )
    assert priority is Priority.HIGH
    assert requires_response is True


def test_an_ordinary_newsletter_is_still_low():
    """The gate must not become a promotion for all bulk mail."""
    priority, requires_response = classify_priority(
        is_newsletter=True, is_urgent=False, body="The Fed held rates.",
        to_addresses=["joel@example.test"], user_email="joel@example.test",
    )
    assert priority is Priority.LOW
    assert requires_response is False
