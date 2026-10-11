"""`source_count` must count publications, not emails.

It was `len(members)`, and the live 2026-09-11 digest shows what that buys:
the highest-scoring story in the whole digest was "Earn $60 for Vodafone App
Feedback" with `source_count: 4` — one survey company that had emailed four
times. "Houthi Advance Threatens Global Trade" claimed 4 and listed the BBC
twice.

The number is not decoration: `DeskFeed.leadStory` picks the desk's lead with
`max(sourceCount)`, and the clustering's whole promise — "one story three
newsletters and two wires all covered reaches you once, with all five
attributed" — is a claim about distinct outlets.
"""

from datetime import UTC, datetime

from app.core.schemas import Item, SourceType
from app.pipeline.digest import _distinct_source_count


def _item(source_name: str, title: str, *, kind=SourceType.EMAIL) -> Item:
    return Item(
        source_type=kind,
        source_name=source_name,
        title=title,
        timestamp=datetime(2026, 9, 11, 9, 0, tzinfo=UTC),
    )


def test_one_sender_emailing_four_times_is_one_source():
    """The live case, exactly."""
    members = [_item("Respondent", f"Study {i}") for i in range(4)]
    assert _distinct_source_count(members) == 1


def test_the_bbc_twice_and_cnbc_once_is_two():
    members = [
        _item("the BBC", "Houthi advance", kind=SourceType.RSS),
        _item("CNBC", "Red Sea shipping", kind=SourceType.RSS),
        _item("the BBC", "Houthi advance, updated", kind=SourceType.RSS),
    ]
    assert _distinct_source_count(members) == 2


def test_the_acceptance_case_still_counts_five():
    """The PRD's own case: three newsletters and two wires on one story."""
    members = [
        _item("Stratechery", "The acquisition"),
        _item("Platformer", "The acquisition"),
        _item("TLDR", "The acquisition"),
        _item("Reuters", "Firm acquires rival", kind=SourceType.RSS),
        _item("the BBC", "Acquisition confirmed", kind=SourceType.RSS),
    ]
    assert _distinct_source_count(members) == 5


def test_the_same_story_under_two_headlines_still_counts_twice():
    """Deliberately NOT deduplicated by title.

    Two publications writing their own headline for one event is exactly
    what this number is for; collapsing on title would erase the signal
    instead of cleaning it.
    """
    members = [
        _item("Reuters", "Firm acquires rival", kind=SourceType.RSS),
        _item("the BBC", "Acquisition confirmed", kind=SourceType.RSS),
    ]
    assert _distinct_source_count(members) == 2


def test_a_single_member_is_one_and_an_empty_cluster_is_zero():
    assert _distinct_source_count([_item("TLDR", "Issue")]) == 1
    assert _distinct_source_count([]) == 0


def test_the_count_and_the_sources_list_answer_different_questions():
    """The invariant the schema used to claim, corrected.

    `sources` is per-article attribution — one entry per cluster member,
    each with its own title and url, so the BBC covering a story twice gives
    two openable rows. `source_count` answers "how many outlets corroborated
    this", and the BBC twice is one outlet.

    Deduplicating `sources` to restore `count == len(sources)` would throw
    away a link the reader can open, so they deliberately differ — and the
    count can only ever be the smaller of the two.
    """
    members = [
        _item("the BBC", "Houthi advance", kind=SourceType.RSS),
        _item("the BBC", "Houthi advance, updated", kind=SourceType.RSS),
        _item("CNBC", "Red Sea shipping", kind=SourceType.RSS),
    ]
    count = _distinct_source_count(members)
    assert count == 2
    assert count < len(members), "the whole point: fewer outlets than articles"
