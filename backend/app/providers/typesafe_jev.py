"""
TypeSafe Jev JudgmentProvider — semantic email triage.

Jev is a System One model: it returns a typed judgment and a calibrated
probability rather than prose, so there is no parse step and nothing to
hallucinate into. That is the whole reason it is here. The thing it replaces,
`classify_priority`, is four lines of keyword matching whose own comments
record it failing twice (#55, #56).

Rule 5 holds by construction: `triage_emails` is handed pre-built state dicts
by `app.pipeline.triage`, which truncates the body to BODY_CHARS. No email
body reaches this file in full, and this file never reads an `Item`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from app.core.providers import Claim, EmailJudgment

log = logging.getLogger(__name__)

# The clock the judgment budget is measured on. A seam rather than a direct
# `time.monotonic` call so a test can move it without moving asyncio's own
# clock, which reads `time.monotonic` for every timer.
_clock = time.monotonic

# Four criteria, so the returned score runs 0.0-3.0 as a position on this
# index. Order matters: `app.pipeline.triage` thresholds against it.
# Claims per request. 100 was measured fine; this bounds blast radius,
# not the API.
CLAIMS_PER_REQUEST = 40

CONSEQUENCE_LEVELS = [
    "Nothing - bulk mail, marketing, or an automated notice needing no action",
    "Minor - useful to know eventually, but nothing turns on it and no date attached",
    "Real - a specific person is waiting, or there is a dated commitment this week",
    "Serious - money, account access, or a same-day obligation is at stake",
]


def _questions() -> dict:
    """The three judgments, built fresh per call.

    Separate questions rather than one combined priority label because they
    are genuinely independent: bulk mail can be serious (a bank's account
    warning) and a personal email can be trivial. Asked together they cost
    one request — the docs are explicit that independent questions over the
    same state run in parallel within a single call.
    """
    from typesafe_sdk import Noul, Score

    return {
        "consequence": Score(
            instructions=(
                "What does it cost the recipient not to hear about this email "
                "in a short morning audio briefing?"
            ),
            criteria=CONSEQUENCE_LEVELS,
        ),
        "awaiting_reply": Noul(
            instructions=(
                "A specific person is waiting for the recipient to reply to "
                "this message."
            )
        ),
        "bulk": Noul(
            instructions=(
                "This was sent to a mailing list or as bulk mail, rather than "
                "written personally to the recipient."
            )
        ),
    }


class JevJudgmentProvider:
    """Satisfies `app.core.providers.JudgmentProvider`.

    Imports the SDK at construction rather than at module import so a machine
    without `typesafe-sdk` installed can still import the pipeline — the same
    reason `build_embedding_provider` imports lazily.
    """

    def __init__(
        self, *, concurrency: int | None = None, api_key: str | None = None
    ) -> None:
        from typesafe_sdk import AsyncTypeSafeClient  # noqa: F401  (probe)

        from app.core.config import get_settings

        settings = get_settings()
        # Explicitly, NOT via the SDK's own environment lookup. The key lives
        # in backend/.env, which pydantic-settings reads into `Settings` —
        # it is never exported into os.environ, so the SDK's fallback finds
        # nothing. On a live run that produced a provider that constructed
        # fine, authenticated on nothing, and degraded every email to the
        # heuristic while the test suite stayed green.
        self._api_key = api_key or settings.typesafe_api_key
        if not self._api_key:
            raise RuntimeError(
                "JevJudgmentProvider needs TYPESAFE_API_KEY; "
                "build_judgment_provider() should have returned None"
            )
        if concurrency is None:
            concurrency = settings.judgment_concurrency
        self._concurrency = max(1, concurrency)
        # A ceiling on the PASS, not on one request. Without it the worst
        # case is ceil(n/concurrency) waves of `timeout` each — 160s for 60
        # emails at the defaults, against a 90-second briefing budget that
        # Phase 1 already spends 71s of. A hung provider would not fail the
        # briefing, it would make it miss the morning.
        self._budget = settings.judgment_total_budget_seconds
        # Shared across every call on this instance — see
        # judgment_briefing_budget_seconds.
        #
        # Counted as time spent INSIDE judgment calls, not time since the
        # first one. It used to be a wall-clock window opened by the first
        # call, and triage and cluster verification make that call before
        # the script exists. The semantic gates run about two minutes later,
        # after script generation, so the window had always closed: every
        # production run checked (2026-10-08 to 10-11) judged 0 of its
        # 67-93 claims, and rule 3's grounding gate only ever ran as its
        # keyword fallback. ROADMAP task 47.
        self._briefing_budget = settings.judgment_briefing_budget_seconds
        self._spent = 0.0
        # Calls in flight, and when the current run of overlapping calls
        # began. Two calls judging at the same moment cost the wall time
        # they overlap, not the sum of both.
        self._in_flight = 0
        self._busy_since = 0.0

    def _remaining(self) -> float:
        """Seconds of judgment budget left for this briefing.

        Zero or less means every remaining call returns immediately and its
        callers fall back to the heuristics, which is the intended
        behaviour rather than a failure.
        """
        return self._briefing_budget - self._spent

    @contextlib.asynccontextmanager
    async def _spending(self):
        """Charge the time inside the block to this briefing's budget.

        Wall time while ANY call is in flight, so overlapping calls are
        charged once. Every call is awaited in turn today; this keeps the
        budget honest for the first change that gathers two of them.
        """
        if self._in_flight == 0:
            self._busy_since = _clock()
        self._in_flight += 1
        try:
            yield
        finally:
            self._in_flight -= 1
            if self._in_flight == 0:
                self._spent += _clock() - self._busy_since

    def _deadline(self) -> float:
        """The effective ceiling for ONE call: whichever bound bites first."""
        return min(self._budget, self._remaining())

    async def judge_claims(
        self, state: dict, claims: list[Claim], *, timeout: float = 20.0
    ) -> dict[str, float | None]:
        """See `JudgmentProvider.judge_claims`.

        Chunked at CLAIMS_PER_REQUEST. 100 claims in one request was
        measured working on 2026-09-21, so the chunk is not a limit the API
        imposes — it is a bound on how much one failure costs, since a
        request that fails takes every claim in it down with it.
        """
        if not claims:
            return {}

        deadline = self._deadline()
        if deadline <= 0:
            log.warning(
                "jev: the %.0fs briefing judgment budget is spent — %d claim(s) "
                "go unjudged and their gates fall back to the heuristics",
                self._briefing_budget,
                len(claims),
            )
            return {c.key: None for c in claims}

        from typesafe_sdk import AsyncTypeSafeClient, Noul

        out: dict[str, float | None] = {c.key: None for c in claims}
        chunks = [
            claims[i : i + CLAIMS_PER_REQUEST]
            for i in range(0, len(claims), CLAIMS_PER_REQUEST)
        ]
        semaphore = asyncio.Semaphore(self._concurrency)

        async with (
            self._spending(),
            AsyncTypeSafeClient(api_key=self._api_key) as client,
        ):

            async def ask(chunk: list[Claim]) -> None:
                # Positional ids, not `Claim.key`: the key is the caller's
                # and may contain anything. Mapped back below.
                questions = {
                    f"c{i}": Noul(instructions=c.instructions)
                    for i, c in enumerate(chunk)
                }
                async with semaphore:
                    try:
                        response = await asyncio.wait_for(
                            client.system_one(state=state, questions=questions),
                            timeout=timeout,
                        )
                    except Exception as exc:
                        log.warning(
                            "jev: %d claim(s) went unjudged: %s", len(chunk), exc
                        )
                        return
                for i, claim in enumerate(chunk):
                    answer = response.nouls.get(f"c{i}")
                    if answer is not None:
                        out[claim.key] = float(answer.noul)

            tasks = [asyncio.create_task(ask(c)) for c in chunks]
            _, pending = await asyncio.wait(tasks, timeout=deadline)
            if pending:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                log.warning(
                    "jev: %d claim chunk(s) did not finish within the %.0fs "
                    "remaining budget and were cancelled",
                    len(pending),
                    deadline,
                )
        return out

    async def triage_emails(
        self, states: list[dict], *, timeout: float = 20.0
    ) -> list[EmailJudgment | None]:
        if not states:
            return []

        deadline = self._deadline()
        if deadline <= 0:
            log.warning(
                "jev: the %.0fs briefing judgment budget is spent before "
                "triage — all %d email(s) keep the classify_priority heuristic",
                self._briefing_budget,
                len(states),
            )
            return [None] * len(states)

        from typesafe_sdk import AsyncTypeSafeClient

        questions = _questions()
        semaphore = asyncio.Semaphore(self._concurrency)
        # Written into by index rather than collected from `gather`, so a
        # judgment that landed before the deadline survives the deadline.
        out: list[EmailJudgment | None] = [None] * len(states)

        async with (
            self._spending(),
            AsyncTypeSafeClient(api_key=self._api_key) as client,
        ):

            async def judge(index: int, state: dict) -> None:
                async with semaphore:
                    try:
                        response = await asyncio.wait_for(
                            client.system_one(state=state, questions=questions),
                            timeout=timeout,
                        )
                        out[index] = _to_judgment(response)
                    except Exception as exc:
                        # Per-item, per the protocol: one unjudged email
                        # costs that email its semantic priority, not the
                        # morning's briefing (rule 7). `Exception` and not
                        # BaseException, so the cancellation below is not
                        # swallowed and reported as a judgment failure.
                        log.warning("jev: judgment failed for one email: %s", exc)

            tasks = [asyncio.create_task(judge(i, s)) for i, s in enumerate(states)]
            _, pending = await asyncio.wait(tasks, timeout=deadline)
            if pending:
                for task in pending:
                    task.cancel()
                # Awaited, not just cancelled: an un-awaited cancelled task
                # leaves the HTTP connection open past the `async with` that
                # is about to close the client underneath it.
                await asyncio.gather(*pending, return_exceptions=True)
                log.warning(
                    "jev: %d of %d judgment(s) did not finish within the %.0fs "
                    "remaining budget and were cancelled — those emails keep "
                    "the classify_priority heuristic",
                    len(pending),
                    len(states),
                    deadline,
                )

        return out


def _to_judgment(response) -> EmailJudgment:
    score = response.scores["consequence"]
    return EmailJudgment(
        consequence=float(score.score),
        confidence=float(score.confidence),
        awaiting_reply=float(response.nouls["awaiting_reply"].noul),
        bulk=float(response.nouls["bulk"].noul),
    )
