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
import logging

from app.core.providers import EmailJudgment

log = logging.getLogger(__name__)

# Four criteria, so the returned score runs 0.0-3.0 as a position on this
# index. Order matters: `app.pipeline.triage` thresholds against it.
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

    async def triage_emails(
        self, states: list[dict], *, timeout: float = 20.0
    ) -> list[EmailJudgment | None]:
        if not states:
            return []

        from typesafe_sdk import AsyncTypeSafeClient

        questions = _questions()
        semaphore = asyncio.Semaphore(self._concurrency)

        async with AsyncTypeSafeClient(api_key=self._api_key) as client:

            async def judge(state: dict) -> EmailJudgment | None:
                async with semaphore:
                    try:
                        response = await asyncio.wait_for(
                            client.system_one(state=state, questions=questions),
                            timeout=timeout,
                        )
                    except Exception as exc:
                        # Per-item, per the protocol: one unjudged email
                        # costs that email its semantic priority, not the
                        # morning's briefing (rule 7).
                        log.warning("jev: judgment failed for one email: %s", exc)
                        return None
                    return _to_judgment(response)

            # return_exceptions so a bug in `_to_judgment` degrades the same
            # way a network failure does, instead of cancelling siblings and
            # taking the whole pass down with it.
            results = await asyncio.gather(
                *(judge(s) for s in states), return_exceptions=True
            )

        out: list[EmailJudgment | None] = []
        for result in results:
            if isinstance(result, BaseException):
                log.warning("jev: judgment raised: %s", result)
                out.append(None)
            else:
                out.append(result)
        return out


def _to_judgment(response) -> EmailJudgment:
    score = response.scores["consequence"]
    return EmailJudgment(
        consequence=float(score.score),
        confidence=float(score.confidence),
        awaiting_reply=float(response.nouls["awaiting_reply"].noul),
        bulk=float(response.nouls["bulk"].noul),
    )
