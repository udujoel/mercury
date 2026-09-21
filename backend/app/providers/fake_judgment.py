"""
Deterministic, offline JudgmentProvider. No network calls.

The lesson from FakeLLMProvider and FakeEmbeddingProvider: a fake that does
not behave like the real thing certifies nothing. A judgment fake returning a
constant would make every triage test vacuous — the bank-alert case and the
marketing case would land on the same priority and the test could not tell a
working threshold from a broken one.

So this is driven BY its input, and calibrated against three real Jev calls
measured on 2026-09-21 (see docs/LOG.md):

    bank alert (#55)        consequence 3.00  awaiting 0.23  bulk 0.57
    "Ready to upgrade?"     consequence 0.00  awaiting 0.12  bulk 0.85
    colleague's direct ask  consequence 1.99  awaiting 0.86  bulk 0.05

It is a keyword model, not a semantic one — it has no idea what the words
mean and will disagree with Jev on anything subtle. That is acceptable for
what it is for: exercising the composition, thresholds and fallback paths
without a network. It is NOT evidence that Jev ranks a real inbox well; only
a live run is.
"""

from __future__ import annotations

from app.core.providers import EmailJudgment

# Money, access, or a same-day obligation — the "Serious" level.
_SERIOUS = (
    "restricted", "suspended", "unauthorised", "unauthorized", "overdue",
    "expires today", "password", "invoice", "payment failed", "declined",
    "locked", "security alert", "fraud", "verify your", "confirm your",
)
# A dated commitment or a named person waiting — the "Real" level.
_REAL = (
    "by thursday", "by friday", "by monday", "tomorrow", "this week",
    "let me know", "can you", "could you", "review", "deadline", "before",
    "waiting", "your thoughts", "sign off",
)
_MARKETING = (
    "unsubscribe", "% off", "save 20", "upgrade your", "ready to",
    "limited time", "newsletter", "webinar", "don't miss", "special offer",
)


# EXACTLY the keys `app.pipeline.triage.email_state` emits. The first
# version of this read "body" and "sender", which that function has never
# produced — it emits "body_excerpt", "sender_name" and "sender_address" —
# so the fake silently judged on the subject line alone and the tests passed
# only because their subjects happened to carry the keywords.
# `test_the_fake_reads_the_body` fails if these drift apart again.
_STATE_TEXT_KEYS = ("subject", "body_excerpt", "sender_name", "sender_address")


def _hay(state: dict) -> str:
    return " ".join(str(state.get(k, "")) for k in _STATE_TEXT_KEYS).lower()


def judge_state(state: dict) -> EmailJudgment:
    """Pure, deterministic state -> EmailJudgment."""
    hay = _hay(state)
    listish = bool(state.get("looks_like_bulk_mail"))
    marketing = sum(t in hay for t in _MARKETING)
    serious = sum(t in hay for t in _SERIOUS)
    real = sum(t in hay for t in _REAL)

    # Serious dominates bulk on purpose — that inversion IS #55.
    if serious:
        consequence = 3.0
    elif real and not marketing:
        consequence = 2.0
    elif marketing or listish:
        consequence = 0.0
    else:
        consequence = 1.0

    bulk = 0.85 if (listish and marketing) else 0.57 if listish else 0.05
    awaiting = 0.86 if (real and not marketing and not listish) else 0.12
    return EmailJudgment(
        consequence=consequence,
        confidence=1.0,
        awaiting_reply=awaiting,
        bulk=bulk,
    )


class FakeJudgmentProvider:
    """Offline JudgmentProvider for tests and keyless development.

    `fail_indices` forces `None` at those positions so the per-item fallback
    to `classify_priority` can be tested without breaking a network.
    """

    def __init__(self, *, fail_indices: frozenset[int] | None = None) -> None:
        self.fail_indices = fail_indices or frozenset()
        self.calls: list[list[dict]] = []

    async def triage_emails(
        self, states: list[dict], *, timeout: float = 20.0
    ) -> list[EmailJudgment | None]:
        self.calls.append(states)
        return [
            None if i in self.fail_indices else judge_state(s)
            for i, s in enumerate(states)
        ]
