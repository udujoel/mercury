"""Transactional mail is bulk, but it is not journalism.

`detect_newsletter` fires on `List-Unsubscribe`, which every bank, payment
app and password-reset mailer sets. One flag was answering two different
questions — how should this be TREATED editorially, and does it belong on a
screen built for published journalism — and the second answer was wrong.

Measured on the live inbox, 2026-09-20: twelve messages flagged
`is_newsletter` and so eligible for the 30-day browse desk, of which seven
were machines. Two financial, one a security alert. Each was eligible for an
LLM-written summary persisted to R2 for a month.

The worse half was quieter. `detect_urgent` refuses anything flagged
`is_newsletter`, for a good reason — a digest reporting someone else's
breach is not your emergency. But a bank alert arrives flagged that way too,
so the one category the owner named as worth waking up for was structurally
incapable of being urgent. ROADMAP task 27.

**Everything here that can go through `message_to_item` does.** The first
cut of this file tested `detect_transactional` directly and hand-built
`raw={"is_transactional": ...}` for the consumers, and review proved by
mutation that deleting the line which writes the flag into `Item.raw` left
all sixteen tests green — the feature could be unplugged from ingestion
entirely without a single failure. That is the same shape as #53's fake
returning `{}`, in the PR that fixed #53.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.schemas import Item, Priority
from app.ingestion.gmail import detect_urgent, message_to_item
from app.pipeline.digest import digest_members
from app.pipeline.generate import _newsletters_for_extraction

USER = "udujoel@example.test"


def _raw(sender: str, subject: str, body: str = "Some body text.") -> dict:
    """A Gmail API message, so tests enter the production path at the top."""
    return {
        "id": f"m-{abs(hash((sender, subject)))}",
        "threadId": "t1",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": USER},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Sun, 20 Sep 2026 04:00:00 +0000"},
                {"name": "List-Unsubscribe", "value": "<https://x.test/u>"},
            ],
            "body": {"data": ""},
        },
        "internalDate": "1789531200000",
        "snippet": body,
    }


def _item(sender: str, subject: str, body: str = "Some body text.") -> Item:
    raw = _raw(sender, subject, body)
    raw["payload"]["body"] = {
        "data": __import__("base64").urlsafe_b64encode(body.encode()).decode()
    }
    return message_to_item(raw, USER)


# ──────────────────────────────────────────────────────────────
# Verbatim from the live inbox on 2026-09-20.
# ──────────────────────────────────────────────────────────────

THE_MACHINES = [
    ("Notifications <notifications@github.com>", "Re: [udujoel/mercury] docs"),
    ("GTBank <gens@gtbank.com>", "Transaction Notification"),
    ("Google <no-reply@accounts.google.com>", "Security alert for an account"),
    ("Trading212 <no-reply@info.trading212.com>", "Deposit successful"),
    ("Billing <donotreply@example.test>", "Your receipt from Example"),
]

THE_JOURNALISM = [
    ("Horizon <mail@joinhorizon.ai>", "Microsoft AI CEO Warns AI Dangers Are Real"),
    ("TAAFT <hi@mail.theresanaiforthat.com>", "This Week in AI"),
    ("Mindstream <hello@mindstream.news>", "We spoke with an AI Engineer"),
    ("Morning Brew <crew@morningbrew.com>", "Gaming Brew"),
    ("1440 <sunday@email.join1440.com>", "Rock 'N' Roll, Weapons in Orbit"),
    # The four that review found gated by news words that are also banking
    # words. Each one cost the desk a real story.
    (
        "Reuters Daily <noreply@reuters.test>",
        "US Announces Troop Withdrawal From Syria",
    ),
    ("The Markup <hello@themarkup.org>", "How to verify your identity online"),
    ("Risky Biz <newsletter@risky.biz>", "Security alert roundup: this week's bugs"),
    ("Fintech Brainfood <hi@fintechbf.test>", "Why payment failed rates matter"),
    # And the weak sender shape that publications genuinely use.
    ("Medium Daily <noreply@medium.com>", "Your daily digest"),
]


@pytest.mark.parametrize(("sender", "subject"), THE_MACHINES)
def test_the_machines_are_gated(sender: str, subject: str):
    item = _item(sender, subject)
    assert item.raw["is_transactional"], f"{sender} reaches the browse desk"
    assert item.raw["transactional_signal"], "a silent classifier cannot be argued with"


@pytest.mark.parametrize(("sender", "subject"), THE_JOURNALISM)
def test_real_publications_survive(sender: str, subject: str):
    """The expensive direction, and the one review caught me getting wrong.

    A desk that loses "US Announces Troop Withdrawal From Syria" to a rule
    about bank withdrawals is worse than one carrying a billing notice.
    """
    item = _item(sender, subject)
    assert not item.raw["is_transactional"], (
        f"{sender} / {subject!r} was gated by " f"{item.raw['transactional_signal']}"
    )


# ──────────────────────────────────────────────────────────────
# Urgency: the narrow gate, and what it must NOT let through.
# ──────────────────────────────────────────────────────────────


def test_a_bank_alert_can_finally_be_urgent():
    """`List-Unsubscribe` made an account-restriction warning a
    "newsletter", and `detect_urgent` refuses newsletters — so the category
    the owner named as the whole point of the section could never reach it.
    """
    before, _ = detect_urgent(
        "Your account has been restricted", "", is_newsletter=True, is_financial=False
    )
    assert not before, "precondition: the newsletter guard suppressed this"

    item = _item("GTBank <gens@gtbank.com>", "Your account has been restricted")
    assert item.raw["is_urgent"]
    assert item.priority is Priority.HIGH
    assert item.requires_response is True


@pytest.mark.parametrize(
    ("sender", "subject", "body"),
    [
        # The PR's own counter-example — which production got WRONG, because it
        # computes is_transactional=True from noreply@ where the first cut of
        # this test hardcoded False.
        (
            "Medium Daily <noreply@medium.com>",
            "Your daily digest",
            "Cloudflare users saw unusual sign-in alerts after the outage.",
        ),
        (
            "Risky Biz <newsletter@risky.biz>",
            "Security alert roundup",
            "A security alert for every Log4j-shaped bug of the week.",
        ),
        (
            "The Markup <hello@themarkup.org>",
            "How to verify your identity online",
            "Verify your account before anyone else does.",
        ),
        (
            "Fintech Brainfood <hi@fintechbf.test>",
            "Why payment failed rates matter",
            "Payment failed rates are the metric nobody tracks.",
        ),
    ],
)
def test_journalism_about_incidents_is_not_an_incident(sender, subject, body):
    """Four of five realistic publications came out HIGH before this.

    The cause was structural rather than a tuning miss: four
    TRANSACTIONAL_SUBJECT_SIGNALS were also URGENT_PATTERNS, so one match
    both removed a message from the desk and promoted it into the
    action-required list.
    """
    item = _item(sender, subject, body)
    assert not item.raw[
        "is_urgent"
    ], f"{subject!r} escalated on {item.raw.get('urgent_signal')!r}"
    assert item.priority is not Priority.HIGH


def test_an_ordinary_colleague_is_not_escalated_by_a_bare_noun_phrase():
    """A bare `security alert` pattern promoted an email that says so
    itself: "no action needed"."""
    item = _item(
        "Sam <sam@work.test>",
        "Ops note",
        "FYI we are rolling out a new security alert system next week. "
        "No action needed.",
    )
    assert not item.raw["is_urgent"]


def test_a_github_notification_about_this_repo_is_not_an_emergency():
    item = _item(
        "Notifications <notifications@github.com>",
        "Re: [udujoel/mercury] feat: lift a security alert to HIGH (PR #56)",
        "action required before merge",
    )
    assert not item.raw["is_urgent"]


# ──────────────────────────────────────────────────────────────
# The consumers, entered from a raw Gmail message.
# ──────────────────────────────────────────────────────────────


def test_transactional_mail_is_off_the_browse_desk():
    from app.pipeline.clustering import Story

    item = _item("GTBank <gens@gtbank.com>", "Transaction Notification")
    story = Story(cluster_id="c1", members=[item], canonical=item, score=1.0)
    assert digest_members(story) == [], (
        "a debit notification would get an LLM-written summary persisted "
        "to R2 for thirty days"
    )


def test_a_real_newsletter_is_still_on_the_browse_desk():
    """The gate must not become "everything"; this is what catches a
    detector that has drifted into rejecting its own input."""
    from app.pipeline.clustering import Story

    item = _item("Morning Brew <crew@morningbrew.com>", "Gaming Brew")
    story = Story(cluster_id="c1", members=[item], canonical=item, score=1.0)
    assert digest_members(story) == [item]


def test_the_newsletters_shelf_is_gated_too():
    """The shelf is the browse surface's other half and needs the same
    gate. Review proved by mutation that reverting it left the suite green.
    """
    from app.core.schemas import BriefingManifest
    from app.pipeline.digest import build_digest

    bank = _item("GTBank <gens@gtbank.com>", "Transaction Notification")
    brew = _item("Morning Brew <crew@morningbrew.com>", "Gaming Brew")
    digest = build_digest(
        briefing_id="2026-09-20",
        stories=[],
        emails=[bank, brew],
        manifest=BriefingManifest(
            briefing_id="2026-09-20",
            generated_at=datetime(2026, 9, 20, tzinfo=UTC),
            duration_ms=1,
            audio_url="/a",
            turns=[],
            segments=[],
            sources=[],
        ),
    )
    shelf = [n.source_name for n in digest.newsletters]
    assert "Morning Brew" in shelf
    assert "GTBank" not in shelf, f"the bank alert is on the shelf: {shelf}"


def test_transactional_mail_never_reaches_the_extractor():
    """There is no journalism inside a debit notification to extract, and
    asking costs prompt budget."""
    bank = _item("GTBank <gens@gtbank.com>", "Transaction Notification")
    brew = _item("Morning Brew <crew@morningbrew.com>", "Gaming Brew")
    handed = _newsletters_for_extraction([bank, brew])
    assert [i.source_name for i in handed] == ["Morning Brew"]


def test_the_flag_is_actually_written_into_the_item():
    """The mutation review killed. Deleting the two lines in
    `message_to_item` that persist this flag unplugged the whole feature
    from ingestion, and all sixteen tests stayed green."""
    item = _item("GTBank <gens@gtbank.com>", "Transaction Notification")
    assert "is_transactional" in item.raw
    assert item.raw["is_transactional"] is True
    assert item.raw["transactional_signal"] == "financial (@gtbank.com)"


def test_a_non_urgent_bank_notice_still_reaches_the_rapid_fire_tail():
    """It must land SOMEWHERE. Review found the GTBank message this whole
    task was named after reaching no surface at all: off the desk, out of
    the newsletter block, and dropped from OTHER MAIL for being a
    newsletter — so the fix had silenced its own headline example.
    """
    from app.core.schemas import SourceType as ST

    bank = _item("GTBank <gens@gtbank.com>", "Transaction Notification")
    assert bank.source_type is ST.EMAIL
    assert bank.raw["is_newsletter"] and bank.raw["is_transactional"]

    spoken_as_stories = {id(e) for e in _newsletters_for_extraction([bank])}
    other_mail = [e for e in [bank] if id(e) not in spoken_as_stories]
    assert other_mail == [bank], (
        "a bank notice that is not urgent enough to escalate must still be "
        "mentioned in the rapid-fire tail rather than vanishing"
    )
