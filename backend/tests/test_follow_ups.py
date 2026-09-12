"""The follow-ups segment, which was written from the literal string "(none)".

Every briefing ever generated contained a follow-ups segment built from
nothing, while the segment was a full citizen everywhere else: in the
`Segment` enum, with a label, a prompt definition, a word budget, required by
the validator, and offered to the user as a toggle reading "Where you owe
someone a reply". The same shape as the `n_stories=0` incident CLAUDE.md
records — and, as there, the data was never missing. `classify_priority` has
set `requires_response` since Phase 1; `ranking.py` read it for scoring and
nothing read it for the segment named after it.
"""

from datetime import UTC, datetime

from app.core.schemas import Item, SourceType
from app.pipeline.script_chain import (
    FOLLOW_UP_LIMIT,
    _follow_up_policy,
    _format_follow_ups,
)


def _email(title: str, *, owed: bool, days_ago: int = 0, sender: str = "Priya") -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=sender,
        author=f"{sender.lower()}@example.com",
        title=title,
        body="...",
        timestamp=datetime(2026, 9, 12, 9, 0, tzinfo=UTC).replace(
            day=12 - days_ago),
        requires_response=owed,
    )


def test_only_emails_awaiting_a_reply_are_listed():
    out = _format_follow_ups(
        [
            _email("Rollout plan — thoughts?", owed=True),
            _email("Weekly newsletter", owed=False),
            _email("Your receipt", owed=False),
        ],
        limit=FOLLOW_UP_LIMIT,
    )
    assert "Rollout plan" in out
    assert "newsletter" not in out
    assert "receipt" not in out


def test_an_empty_inbox_is_told_to_the_model_as_an_instruction():
    """The segment is structural, so the model writes SOMETHING here.

    A bare "(none)" invites it to invent an errand; being told plainly that
    the inbox is clear, and told not to invent one, cannot be misread.
    """
    clear = [_email("FYI", owed=False)]
    assert _format_follow_ups(clear, limit=FOLLOW_UP_LIMIT) == (
        "(no emails awaiting a reply)")
    policy = _follow_up_policy(clear, limit=FOLLOW_UP_LIMIT)
    assert "Nothing in the inbox is awaiting a reply" in policy
    assert "do not invent" in policy


def test_mercurys_own_statements_sit_outside_the_untrusted_fence():
    """The mistake this file documents, and nearly repeated.

    `_inbox_policy` is appended AFTER `_fence_untrusted(...)` because a rule
    about the data, fenced together with the data, tells the model to obey it
    and to distrust it in the same breath. The follow-ups policy has to be
    built the same way — so it must live in its own function and not be
    baked into the formatted block.
    """
    import inspect

    from app.pipeline import script_chain

    body = inspect.getsource(script_chain._format_follow_ups)
    assert "do not invent" not in body, (
        "Mercury's instruction to the model has leaked inside the fenced block")

    call = inspect.getsource(script_chain.generate_script)
    fenced = call.split("follow_ups=(", 1)[1].split("),", 1)[0]
    assert "_fence_untrusted(" in fenced
    assert fenced.index("_follow_up_policy(") > fenced.index("_fence_untrusted(")


def test_oldest_first_because_overdue_is_the_only_useful_order():
    out = _format_follow_ups(
        [
            _email("Newest", owed=True, days_ago=0),
            _email("Oldest", owed=True, days_ago=5),
            _email("Middle", owed=True, days_ago=2),
        ],
        limit=FOLLOW_UP_LIMIT,
    )
    assert out.index("Oldest") < out.index("Middle") < out.index("Newest")


def test_the_cap_counts_what_it_cut_rather_than_hiding_it():
    owed = [_email(f"Question {i}", owed=True, days_ago=i) for i in range(9)]

    named = _format_follow_ups(owed, limit=FOLLOW_UP_LIMIT).splitlines()
    assert len(named) == FOLLOW_UP_LIMIT, "the fenced list is capped"

    policy = _follow_up_policy(owed, limit=FOLLOW_UP_LIMIT)
    assert f"{9 - FOLLOW_UP_LIMIT} further email(s)" in policy
    assert "never invent" in policy


def test_no_policy_line_when_nothing_was_cut():
    owed = [_email("Only one", owed=True)]
    assert _follow_up_policy(owed, limit=FOLLOW_UP_LIMIT) == ""


def test_no_bodies_reach_the_segment():
    """The inbox segment already quotes them.

    Repeating a body here would spend the follow-ups budget re-reading mail
    the listener just heard, and it widens rule 5's exposure for no gain.
    """
    item = _email("Can you confirm?", owed=True)
    item.body = "SECRET-BODY-TEXT that must not be repeated here"
    out = _format_follow_ups([item], limit=FOLLOW_UP_LIMIT)
    assert "SECRET-BODY-TEXT" not in out
    assert "Can you confirm?" in out
    assert "Priya" in out


def test_the_prompt_no_longer_receives_the_literal_none():
    """The regression itself, asserted at the call site's own boundary."""
    import inspect

    from app.pipeline import script_chain

    source = inspect.getsource(script_chain.generate_script)
    assert 'follow_ups="(none)"' not in source, (
        "the follow-ups segment is back to being written from nothing"
    )
    assert "_format_follow_ups(" in source
