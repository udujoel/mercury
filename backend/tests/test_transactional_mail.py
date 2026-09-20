"""Transactional mail is bulk, but it is not journalism.

`detect_newsletter` fires on `List-Unsubscribe`, which every bank, payment
app and password-reset mailer sets. One flag was answering two different
questions — how should this be TREATED editorially, and does it belong on a
screen built for published journalism — and the second answer was wrong.

Measured on the live inbox, 2026-09-20: twelve messages flagged
`is_newsletter` and so eligible for the 30-day browse desk, of which SEVEN
were machines. Two financial, one a security alert. Each was eligible for an
LLM-written summary persisted to R2 for a month.

The worse half was quieter. `detect_urgent` refuses anything flagged
`is_newsletter`, for a good reason — a digest reporting someone else's
breach is not your emergency. But a bank alert arrives flagged that way too,
so the one category the owner named as worth waking up for was structurally
incapable of being urgent. ROADMAP task 27.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.schemas import Item, Priority, SourceType
from app.ingestion.gmail import (
    classify_priority,
    detect_transactional,
    detect_urgent,
)
from app.pipeline.digest import digest_members
from app.pipeline.generate import _newsletters_for_extraction


def _headers(sender: str) -> dict[str, str]:
    return {"From": sender, "List-Unsubscribe": "<https://example.test/u>"}


# Verbatim from the live inbox on 2026-09-20 — the seven that were eligible
# for the browse desk and should not have been.
THE_MACHINES = [
    ("Notifications <notifications@github.com>", "Re: [udujoel/mercury] docs: ..."),
    ("Google <noreply-accounts@google.com>", "You shared some Google Account data"),
    ("GTBank <gens@gtbank.com>", "Transaction Notification"),
    ("Qoder <notice-noreply@qoder.com>", "Qoder Credit Consumption Rate Update"),
    ("Google <no-reply@accounts.google.com>", "Security alert for an account"),
    ("Trading212 <no-reply@info.trading212.com>", "Deposit successful"),
    ("Billing <donotreply@example.test>", "Your receipt from Example"),
]

# ...and the five real publications from the same inbox, which must survive.
THE_JOURNALISM = [
    ("Horizon <mail@joinhorizon.ai>", "Microsoft AI CEO Warns AI Dangers Are Genuine"),
    ("TAAFT <hi@mail.theresanaiforthat.com>", "This Week in AI"),
    ("Mindstream <hello@mindstream.news>", "We spoke with an AI Engineer"),
    ("Morning Brew <crew@morningbrew.com>", "Gaming Brew"),
    ("1440 <sunday@email.join1440.com>", "Rock 'N' Roll, Weapons in Orbit"),
]


@pytest.mark.parametrize(("sender", "subject"), THE_MACHINES)
def test_the_machines_are_recognised(sender: str, subject: str):
    is_transactional, signal = detect_transactional(_headers(sender), subject)
    assert is_transactional, f"{sender} / {subject!r} reaches the browse desk"
    assert signal, "a classifier nobody can argue with is one nobody can fix"


@pytest.mark.parametrize(("sender", "subject"), THE_JOURNALISM)
def test_real_publications_are_not_swept_up(sender: str, subject: str):
    """The expensive direction. A desk that loses the five newsletters Joel
    actually reads is worse than one carrying two bank alerts."""
    is_transactional, signal = detect_transactional(_headers(sender), subject)
    assert not is_transactional, f"{sender} was gated by {signal}"


def test_a_bank_alert_can_finally_be_urgent():
    """The structural half, and the reason this is not just desk hygiene.

    `List-Unsubscribe` made an account-restriction warning a "newsletter",
    and `detect_urgent` refuses newsletters — so the category the owner
    named as the whole point of the urgent section could never reach it.
    """
    subject = "Your account has been restricted"
    before, _ = detect_urgent(subject, "", is_newsletter=True)
    assert not before, "precondition: the newsletter guard suppressed this"

    after, signal = detect_urgent(
        subject, "", is_newsletter=True, is_transactional=True
    )
    assert after and signal, "a restricted account has to reach the reader"

    priority, requires_response = classify_priority(
        is_newsletter=True, is_urgent=after, body="",
        to_addresses=[], user_email="joel@example.test",
    )
    assert (priority, requires_response) == (Priority.HIGH, True)


def test_a_newsletter_reporting_someone_elses_breach_is_still_not_urgent():
    """The guard this exempts from must keep working for what it was for.

    A digest whose body reads "Cloudflare users saw unusual sign-in alerts"
    is journalism about an incident, not an incident. It is not
    transactional, so the exemption does not reach it.
    """
    is_urgent, _ = detect_urgent(
        "This week in security",
        "Cloudflare users saw unusual sign-in alerts after the outage.",
        is_newsletter=True, is_transactional=False,
    )
    assert not is_urgent


def _mail(*, transactional: bool) -> Item:
    return Item(
        source_type=SourceType.EMAIL, source_name="GTBank",
        title="Transaction Notification", body="Your account was debited.",
        timestamp=datetime(2026, 9, 20, 4, tzinfo=UTC),
        raw={"is_newsletter": True, "is_transactional": transactional},
    )


def test_transactional_mail_is_off_the_browse_desk():
    from app.pipeline.clustering import Story

    item = _mail(transactional=True)
    story = Story(cluster_id="c1", members=[item], canonical=item, score=1.0)
    assert digest_members(story) == [], (
        "a debit notification would get an LLM-written summary persisted "
        "to R2 for thirty days"
    )


def test_transactional_mail_never_reaches_the_extractor():
    """There is no journalism inside a debit notification to extract, and
    asking costs prompt budget and sends the body to Gemini (rule 5)."""
    handed = _newsletters_for_extraction(
        [_mail(transactional=True), _mail(transactional=False)]
    )
    assert len(handed) == 1
    assert not handed[0].raw["is_transactional"]
