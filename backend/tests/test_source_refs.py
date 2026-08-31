"""
Grounding trail regression tests.

CLAUDE.md rule 3 says every factual claim must be grounded in the source
data, and PRD F-19 says every item mentioned in audio needs a tappable show-
notes entry. Both depend on `_build_source_refs` linking turns back to the
emails and events they discuss.

These tests exist because the end-to-end test cannot cover this: it drives
the pipeline with `FakeLLMProvider`, whose script is canned and therefore
talks about people and subjects that are not in the Gmail/Calendar fixtures.
The e2e run correctly produces zero SourceRefs and logs a warning about it.
That is honest behaviour for a fake that ignores its input, but it means the
matcher itself is only actually exercised here, against aligned data.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.schemas import CalendarEvent, Item, Segment, SourceType, Turn
from app.pipeline.script_chain import _build_source_refs, _mentions


def _email(title: str, sender: str) -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name=sender,
        title=title,
        body="",
        timestamp=datetime(2026, 8, 21, 7, 0, tzinfo=UTC),
    )


def _event(title: str) -> CalendarEvent:
    return CalendarEvent(
        id=title,
        title=title,
        start=datetime(2026, 8, 21, 16, 0, tzinfo=UTC),
        end=datetime(2026, 8, 21, 17, 0, tzinfo=UTC),
    )


def _turn(text: str) -> Turn:
    return Turn(speaker="A", segment=Segment.INBOX, text=text)


# ── the matcher ───────────────────────────────────────────────


def test_matches_exact_title():
    assert _mentions("First up, the Q3 Budget Review at four.", "Q3 Budget Review")


def test_matches_paraphrased_title():
    """Hosts paraphrase. An exact-substring matcher fails here, which is why
    the matcher is token-based."""
    assert _mentions(
        "Maria sent over the Q3 budget review ahead of Friday.",
        "Q3 budget review before Friday",
        "Maria Chen",
    )


def test_matches_sender_first_name_only():
    """Hosts say 'Maria', not 'Maria Chen'."""
    assert _mentions(
        "Maria needs an answer before the review.", "Unrelated subject", "Maria Chen"
    )


def test_does_not_match_unrelated_turn():
    assert not _mentions(
        "The weather looks clear and warm all weekend.",
        "Q3 budget review before Friday",
        "Maria Chen",
    )


def test_does_not_match_on_a_single_common_token():
    """One incidental token overlap must not manufacture a false citation —
    a wrong source ref is worse than none, because it looks verified."""
    assert not _mentions(
        "Nothing else worth your time.",
        "Flash Sale: 30% off everything",
        "BrandCo Deals",
    )


# ── the builder ───────────────────────────────────────────────


def test_builds_refs_for_email_and_event():
    turns = [
        _turn("First up, the Q3 Budget Review at four this afternoon."),
        _turn("Maria sent the Q3 budget review over last night."),
        _turn("Otherwise the morning is clear."),
    ]
    emails = [_email("Q3 budget review before Friday", "Maria Chen")]
    events = [_event("Q3 Budget Review")]

    refs = _build_source_refs(turns, emails, events)

    assert refs, "aligned data must produce a grounding trail"
    assert {r.turn_index for r in refs} <= {0, 1}, "turn 2 mentions nothing"
    assert any(r.source_type is SourceType.CALENDAR for r in refs)
    assert any(r.source_type is SourceType.EMAIL for r in refs)


def test_turn_indices_point_at_the_right_turns():
    """The manifest seeks by turn_index, so an off-by-one here sends the
    show-notes tap to the wrong moment in the audio."""
    turns = [
        _turn("Good morning. Nothing to report yet."),
        _turn("Signals Weekly led with a piece on aggregation."),
    ]
    emails = [_email("This Week in Signals", "Signals Weekly")]

    refs = _build_source_refs(turns, emails, [])

    assert [r.turn_index for r in refs] == [1]


def test_empty_trail_is_warned_not_silent(caplog):
    """A blank sources list means nothing in the briefing can be checked.
    That is a rule 3 failure and must be visible in the logs."""
    turns = [_turn("Something entirely unrelated to any source.")]
    emails = [_email("Q3 budget review before Friday", "Maria Chen")]

    with caplog.at_level("WARNING"):
        refs = _build_source_refs(turns, emails, [])

    assert refs == []
    assert any("no SourceRefs matched" in r.message for r in caplog.records)


def test_no_warning_when_there_was_nothing_to_match():
    """An empty inbox and empty calendar legitimately produce no refs."""
    with_no_sources = _build_source_refs([_turn("Quiet morning.")], [], [])
    assert with_no_sources == []


# ── sender-name matching discipline ───────────────────────────


def test_a_stop_word_in_a_sender_name_does_not_match_everything():
    """"The Verge" splits into {"the", "verge"}, and the sender loop accepted
    any part longer than two characters — so "the" matched nearly every turn
    in the briefing. The Verge collected a SourceRef on cold_open, weather
    and sign_off: show notes filled with junk, and the grounding audit trail
    credited a publication for claims it had nothing to do with.
    """
    assert not _mentions(
        "Good morning Joel. It's Friday, and the day ahead looks quiet.",
        "Apple pushes back foldable iPhone to 2028",
        "The Verge",
    )


def test_a_distinctive_part_of_the_sender_name_still_matches():
    """The discipline must not cost the signal it was protecting. "Verge" on
    its own is still a strong, low-noise match, and hosts do say "Priya"
    rather than "Priya Raman"."""
    assert _mentions(
        "Verge is reporting the foldable slipped again.",
        "Apple pushes back foldable iPhone to 2028",
        "The Verge",
    )


def test_a_two_letter_sender_part_does_not_match():
    """Length discipline, same as `_distinctive_tokens`: an initial is not
    evidence of anything."""
    assert not _mentions(
        "So that's the shape of the morning.",
        "Quarterly numbers",
        "A B Consulting",
    )


def test_a_sender_made_entirely_of_stop_words_falls_through_to_the_title():
    """Nothing usable in the sender is not the same as a match. The matcher
    falls back to title tokens rather than matching on filler."""
    assert not _mentions(
        "And that's your weather for the day.",
        "Quarterly numbers from the finance team",
        "The Update",
    )


def test_full_sender_name_still_matches_verbatim():
    """The whole name is unambiguous however common its parts are, so the
    verbatim check ahead of the per-part loop is untouched."""
    assert _mentions(
        "The Verge has more on that.",
        "Unrelated subject",
        "The Verge",
    )


def test_a_junk_ref_does_not_reach_the_manifest():
    """The same bug at the level the user sees it: a publication credited on
    the weather and the sign-off."""
    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN,
             text="Good morning Joel, and welcome to the briefing."),
        Turn(speaker="B", segment=Segment.WEATHER,
             text="Clear all day, with a high of twenty-four."),
        Turn(speaker="A", segment=Segment.SIGN_OFF,
             text="That's the morning. Have a good one."),
    ]
    emails = [_email("Apple pushes back foldable iPhone to 2028", "The Verge")]
    assert _build_source_refs(turns, emails, []) == []
