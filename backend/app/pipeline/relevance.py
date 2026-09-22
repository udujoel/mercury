"""
Whether a story matters to THIS person, asked rather than matched.

`ranking.score_stories` weighs five signals. Four of them are structural —
recency decays, corroboration counts distinct outlets, interest is a cosine
against a centroid. The fifth, `personal`, is a substring match, and it is
the one that has already gone wrong twice.

Review of #58 found the unanchored version worth 0.28 — more than the whole
interest term — and following "X" matched 10 of 25 stories because
"extension" contains an x, putting a Hacker News dark-mode post above a
Russian missile strike. "AI" matched "said", "again", "chair" and
"Ukraine". The fix was a three-character floor and a word boundary, which
stops the false positives and does nothing about the false negatives: a
story is still invisible to that term unless it literally contains the
string. Follow "TypeSafe" and a piece about Jev does not match. Meet
someone from Aveiro Labs at 11:00 and a story about their funding round
does not match.

So this asks instead. One Noul per story against a description of the
person's day, composed back into the SAME [0, 1] range the lexical term
already produced, at the SAME weight. Nothing else about ranking moves:
`w_personal` is untouched, the other four terms are untouched, and a story
with no judgment keeps the lexical score it would have had (rule 7).
"""

from __future__ import annotations

import logging

from app.core.config import get_settings
from app.core.providers import Claim, JudgmentProvider
from app.core.schemas import CalendarEvent, Story

log = logging.getLogger(__name__)

# Enough of a story to judge relevance against a person's day. Titles carry
# most of it; the excerpt is there for the cases where the title is a pun.
EXCERPT_CHARS = 300

# A briefing ranks a few hundred stories at most, but the claim count is
# what costs, so this is a ceiling on the pass rather than on the day.
# Stories beyond it keep the lexical score — and the drop is logged, since
# a silent cap reads as "we judged everything".
MAX_JUDGED = 120


def _person(
    events: list[CalendarEvent],
    followed: list[str] | None,
    interests: list[str] | None,
) -> dict:
    """The day, as the judgment sees it.

    Attendee names and followed entities are the same inputs the lexical
    term uses — the difference is that they are given as CONTEXT to reason
    about rather than as strings to find.
    """
    settings = get_settings()
    return {
        "name": settings.user_name,
        "meeting_today": sorted({
            attendee for event in events for attendee in event.attendees
        })[:40],
        "on_the_calendar_today": [e.title for e in events if not e.is_background][:20],
        "follows": [f for f in (followed or []) if f.strip()][:40],
        "interests": [i for i in (interests or []) if i.strip()][:40],
    }


def _summarise(story: Story) -> dict:
    item = story.canonical
    return {
        "source": item.source_name,
        "title": item.title,
        "excerpt": item.body[:EXCERPT_CHARS],
    }


async def judge_personal_relevance(
    stories: list[Story],
    *,
    events: list[CalendarEvent],
    followed_entities: list[str] | None,
    interests: list[str] | None,
    provider: JudgmentProvider | None,
) -> dict[str, float]:
    """P(this story matters to this person today), by `cluster_id`.

    A story missing from the returned map has no judgment and must keep its
    lexical score — the caller treats absence as "no opinion", never as
    "not relevant". Never raises.
    """
    if provider is None or not stories:
        return {}

    judged = stories[:MAX_JUDGED]
    if len(stories) > MAX_JUDGED:
        log.warning(
            "relevance: %d stories is over the %d this pass budgets for; "
            "judging the %d highest-ranked and leaving %d on the lexical "
            "personal score",
            len(stories), MAX_JUDGED, MAX_JUDGED, len(stories) - MAX_JUDGED,
        )

    person = _person(events, followed_entities, interests)
    if not (person["meeting_today"] or person["follows"] or person["interests"]):
        # Nothing to be relevant TO. The lexical term scores 0.5 for email
        # and 0.0 otherwise in this case, and a judgment with no context to
        # reason from would be an opinion about the news in general.
        log.info(
            "relevance: no attendees, follows or interests configured — "
            "leaving all %d story(ies) on the lexical personal score",
            len(stories),
        )
        return {}

    state = {"person": person, "stories": [_summarise(s) for s in judged]}
    claims = [
        Claim(
            key=f"relevance:{index}",
            instructions=(
                f"`stories[{index}]` matters personally to the person "
                "described in `person`, in a morning briefing made for them "
                "today. It matters if it touches someone they are meeting, "
                "an organisation or subject they follow, or a stated "
                "interest — including where the connection is by meaning "
                "rather than by the same words. Being important news in "
                "general is NOT enough."
            ),
        )
        for index in range(len(judged))
    ]

    settings = get_settings()
    try:
        answers = await provider.judge_claims(
            state, claims, timeout=settings.judgment_timeout_seconds
        )
    except Exception as exc:
        log.warning(
            "relevance judgment failed (%s) — all %d story(ies) keep the "
            "lexical personal score", exc, len(stories),
        )
        return {}

    out: dict[str, float] = {}
    for index, story in enumerate(judged):
        probability = answers.get(f"relevance:{index}")
        if probability is not None:
            out[story.cluster_id] = probability

    unjudged = len(judged) - len(out)
    (log.warning if out == {} else log.info)(
        "relevance: %d of %d story(ies) judged, %d left on the lexical score",
        len(out), len(judged), unjudged + max(0, len(stories) - len(judged)),
    )
    return out


__all__ = ["judge_personal_relevance"]
