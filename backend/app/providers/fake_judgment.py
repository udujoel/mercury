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

import re

from app.core.providers import Claim, EmailJudgment

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


# ──────────────────────────────────────────────────────────────
# Claims
# ──────────────────────────────────────────────────────────────

# A claim does not carry the text it is about — it POINTS at part of the
# state with a backticked path like `lines[3]` or `segments.inbox`, and the
# model resolves it. So this fake resolves it too.
#
# The first version read the claim's instructions and nothing else, which
# made every claim look identical and every gate test pass vacuously. That
# is the same defect review found in this file's email half on #60 (a fake
# reading keys the real state never had), so it is worth saying plainly:
# a fake has to read what the real thing reads, or it certifies nothing.
_PATH_RE = re.compile(r"`(lines\[\d+\]|segments\.[A-Za-z_][A-Za-z0-9_]*)`")

def _resolve(state: dict, path: str) -> str:
    """`lines[3]` / `segments.inbox` -> that piece of the state, or ""."""
    match = re.fullmatch(r"lines\[(\d+)\]", path)
    if match:
        lines = state.get("lines") or []
        index = int(match.group(1))
        return str(lines[index]) if index < len(lines) else ""
    match = re.fullmatch(r"segments\.(.+)", path)
    if match:
        return str((state.get("segments") or {}).get(match.group(1), ""))
    return ""


# The three gates have OPPOSITE polarity: grounding and challenge flag on a
# LOW probability ("this is not supported", "nobody pushed back"), relay
# flags on a HIGH one ("the hosts did relay it"). So a single "this marker
# means false" rule cannot serve all three — a clean line would come back
# flagged as a relay. The fake therefore knows the three claim families by
# their key prefix, which is a coupling to this pipeline and is the point:
# it is this pipeline's test double, not a general-purpose stub.
#
# `_NORMAL` is what each family looks like when nothing is wrong;
# `_FLIP` is the marker a test puts in a turn to make that family abnormal.
_NORMAL = {"ground": 0.98, "relay": 0.02, "challenge": 0.98}
_FLIP = {
    "ground": "__UNGROUNDED__",
    "relay": "__RELAYS__",
    "challenge": "__NO_CHALLENGE__",
}


def judge_claim(claim: Claim, state: dict | None = None) -> float:
    """Pure, deterministic (Claim, state) -> P(true).

    Resolves every backticked path in the claim against the state, then
    answers the family's normal value unless the referenced text carries
    that family's flip marker. Tests put the marker in the turn they want
    flagged, so the fake stays honest about WHICH line it was asked about
    without pretending to understand English.
    """
    state = state or {}
    referenced = " ".join(
        _resolve(state, path) for path in _PATH_RE.findall(claim.instructions)
    )
    haystack = referenced or claim.instructions
    family = claim.key.split(":", 1)[0]
    normal = _NORMAL.get(family, 0.98)
    marker = _FLIP.get(family)
    if marker and marker in haystack:
        return round(1.0 - normal, 2)
    return normal


class FakeClaimJudge:
    """Offline `judge_claims` half of the provider protocol.

    `unjudged_keys` forces `None` for those claims so a gate's
    "not judged" path can be tested separately from its "judged false" one
    — the two lead to opposite decisions.
    """

    def __init__(self, *, unjudged_keys: frozenset[str] | None = None) -> None:
        self.unjudged_keys = unjudged_keys or frozenset()
        self.claim_calls: list[tuple[dict, list[Claim]]] = []

    async def judge_claims(
        self, state: dict, claims: list[Claim], *, timeout: float = 20.0
    ) -> dict[str, float | None]:
        self.claim_calls.append((state, claims))
        return {
            c.key: (
                None if c.key in self.unjudged_keys else judge_claim(c, state)
            )
            for c in claims
        }


class FakeJudgmentProvider(FakeClaimJudge):
    """Offline JudgmentProvider for tests and keyless development.

    `fail_indices` forces `None` at those positions so the per-item fallback
    to `classify_priority` can be tested without breaking a network.
    """

    def __init__(
        self,
        *,
        fail_indices: frozenset[int] | None = None,
        unjudged_keys: frozenset[str] | None = None,
    ) -> None:
        super().__init__(unjudged_keys=unjudged_keys)
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
