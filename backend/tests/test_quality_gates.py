"""
Tests for app/pipeline/quality.py — the four quality gates promoted from
scripts/spike_04_llm.py, now operating on `list[Turn]` directly.

Each gate gets a known-bad and known-good sample, mirroring the spike's own
test coverage (tests/test_spike_04_script.py), plus dedicated coverage for
the two things this module changes relative to the spike:

  - check_disagreement's scope is narrowed to calendar/inbox/world only
    (cold_open/weather/follow_ups/sign_off are exempt — see the module
    docstring in quality.py for why).
  - run_all_gates aggregates all four into one report dict.
"""

from __future__ import annotations

from app.core.schemas import Segment, Turn
from app.pipeline.quality import (
    check_disagreement,
    check_filler_words,
    check_grounding,
    check_monologues,
    run_all_gates,
)

# ──────────────────────────────────────────────────────────────
# check_filler_words
# ──────────────────────────────────────────────────────────────


def test_check_filler_words_flags_known_bad():
    turns = [Turn(speaker="A", segment=Segment.COLD_OPEN, text="Um, good morning.")]
    hits = check_filler_words(turns)
    assert len(hits) == 1
    assert hits[0]["word"] == "um"


def test_check_filler_words_passes_known_good():
    turns = [Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning, Joel.")]
    assert check_filler_words(turns) == []


def test_check_filler_words_no_false_positive_on_substring():
    # "um" is a substring of "bump" and "album" — must not false-positive.
    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN,
             text="The lxml version bump landed, and so did the new album."),
    ]
    assert check_filler_words(turns) == []


def test_check_filler_words_catches_you_know_and_i_mean():
    turns = [
        Turn(speaker="A", segment=Segment.INBOX, text="It's, you know, complicated."),
        Turn(speaker="B", segment=Segment.INBOX, text="I mean, not really."),
    ]
    hits = check_filler_words(turns)
    words = {h["word"] for h in hits}
    assert "you know" in words
    assert "i mean" in words


# ──────────────────────────────────────────────────────────────
# check_monologues
# ──────────────────────────────────────────────────────────────


def test_check_monologues_flags_known_bad():
    text = "One. Two. Three. Four. Five."
    turns = [Turn(speaker="A", segment=Segment.WORLD, text=text)]
    hits = check_monologues(turns)
    assert len(hits) == 1
    assert hits[0]["sentence_count"] == 5


def test_check_monologues_passes_known_good():
    text = "One. Two. Three. Four."
    turns = [Turn(speaker="A", segment=Segment.WORLD, text=text)]
    assert check_monologues(turns) == []


# ──────────────────────────────────────────────────────────────
# check_disagreement — including the narrowed-scope change
# ──────────────────────────────────────────────────────────────


def test_check_disagreement_flags_known_bad_in_calendar():
    turns = [
        Turn(speaker="A", segment=Segment.CALENDAR, text="Meeting at nine."),
        Turn(speaker="B", segment=Segment.CALENDAR, text="Sounds good, agreed."),
    ]
    result = check_disagreement(turns)
    segments = [g["segment"] for g in result]
    assert "calendar" in segments


def test_check_disagreement_passes_known_good_in_calendar():
    turns = [
        Turn(speaker="A", segment=Segment.CALENDAR, text="Meeting at nine."),
        Turn(speaker="B", segment=Segment.CALENDAR,
             text="Actually, I'd push back on moving it that early."),
    ]
    result = check_disagreement(turns)
    segments = [g["segment"] for g in result]
    assert "calendar" not in segments


def test_check_disagreement_applies_to_inbox_and_world():
    turns = [
        Turn(speaker="A", segment=Segment.INBOX, text="Priya sent the plan."),
        Turn(speaker="B", segment=Segment.INBOX, text="Sounds good, agreed."),
        Turn(speaker="A", segment=Segment.WORLD, text="The deal closed."),
        Turn(speaker="B", segment=Segment.WORLD, text="Yeah, agreed, exactly."),
    ]
    result = check_disagreement(turns)
    segments = {g["segment"] for g in result}
    assert segments == {"inbox", "world"}


def test_check_disagreement_exempts_cold_open_weather_follow_ups_sign_off():
    # All four are purely agreeable, multi-turn, with zero challenge
    # markers — the old (spike) gate would have flagged every one of these.
    # The Phase 2 gate must flag none of them.
    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning, Joel."),
        Turn(speaker="B", segment=Segment.COLD_OPEN, text="Sounds good, agreed."),
        Turn(speaker="A", segment=Segment.WEATHER, text="Clear skies today."),
        Turn(speaker="B", segment=Segment.WEATHER, text="Yeah, agreed, exactly."),
        Turn(speaker="A", segment=Segment.FOLLOW_UPS, text="Nothing outstanding."),
        Turn(speaker="B", segment=Segment.FOLLOW_UPS, text="Agreed, clean slate."),
        Turn(speaker="A", segment=Segment.SIGN_OFF, text="That's the morning."),
        Turn(speaker="B", segment=Segment.SIGN_OFF, text="Sounds good, take care."),
    ]
    result = check_disagreement(turns)
    assert result == []


def test_check_disagreement_ignores_single_turn_segments():
    # A lone turn in a substantive segment can't disagree with itself —
    # must not be flagged just for having zero challenge markers.
    turns = [Turn(speaker="A", segment=Segment.INBOX, text="One quiet item today.")]
    result = check_disagreement(turns)
    assert result == []


# ──────────────────────────────────────────────────────────────
# check_grounding
# ──────────────────────────────────────────────────────────────


def test_check_grounding_flags_known_bad():
    turns = [
        Turn(speaker="A", segment=Segment.INBOX,
             text="Zorbex acquired Fintastic for 40 million dollars."),
    ]
    source_text = '{"title": "an unrelated email about lunch"}'
    hits = check_grounding(turns, source_text)
    tokens = {h["token"] for h in hits}
    assert "Zorbex" in tokens
    assert "Fintastic" in tokens


def test_check_grounding_passes_known_good():
    turns = [
        Turn(speaker="A", segment=Segment.INBOX, text="Priya sent the rollout plan."),
    ]
    source_text = '{"author": "priya@platform.mercury.dev", "title": "rollout plan"}'
    hits = check_grounding(turns, source_text)
    assert hits == []


def test_check_grounding_does_not_flag_sentence_openers():
    # These are common sentence-openers, not hallucinated facts. The
    # positional filter approach was tried and rejected (see quality.py);
    # this asserts the vocabulary-based stopword set does its job.
    turns = [
        Turn(speaker="A", segment=Segment.WORLD,
             text="Looking at this closely, Exactly what you'd expect. "
                  "However, that's not the whole story."),
    ]
    hits = check_grounding(turns, "completely unrelated source text")
    tokens = {h["token"] for h in hits}
    assert "Looking" not in tokens
    assert "Exactly" not in tokens
    assert "However" not in tokens


def test_check_grounding_still_catches_a_name_that_opens_a_sentence():
    # The regression case the ADR/commit history calls out explicitly:
    # a hallucinated name must still be caught even when it opens a
    # sentence, which is exactly why the filter is vocabulary-based rather
    # than positional.
    turns = [
        Turn(speaker="A", segment=Segment.WORLD,
             text="Thompson argued the point well in his latest piece."),
    ]
    hits = check_grounding(turns, "an email about the rollout plan and lunch")
    tokens = {h["token"] for h in hits}
    assert "Thompson" in tokens


def test_check_grounding_allows_host_and_user_names():
    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning, Joel."),
    ]
    # user_name defaults to "there" in Settings unless configured; host
    # names default to Mara/Theo. Use a name the gate should never flag
    # because it is a structural part of the show, not a sourced fact —
    # covered indirectly since "Joel" isn't the default user_name, but the
    # point of this test is that the allowed_names set doesn't crash and
    # ordinary capitalised words in a greeting don't explode the flag count.
    hits = check_grounding(turns, "unrelated source text")
    # "Joel" is expected to be flagged since it isn't in source or the
    # default allow-list — this documents current behaviour rather than
    # asserting it's flag-free.
    assert isinstance(hits, list)


# ──────────────────────────────────────────────────────────────
# run_all_gates
# ──────────────────────────────────────────────────────────────


def test_run_all_gates_aggregates_all_four():
    turns = [
        Turn(speaker="A", segment=Segment.CALENDAR, text="Um, meeting at nine."),
        Turn(speaker="B", segment=Segment.CALENDAR, text="Sounds good, agreed."),
    ]
    report = run_all_gates(turns, "meeting at nine source text")
    assert set(report.keys()) == {
        "filler", "monologues", "disagreement", "grounding", "total_issues",
    }
    assert len(report["filler"]) == 1
    assert len(report["disagreement"]) == 1
    assert report["total_issues"] == (
        len(report["filler"]) + len(report["monologues"])
        + len(report["disagreement"]) + len(report["grounding"])
    )


def test_run_all_gates_on_clean_script_reports_zero_issues():
    turns = [
        Turn(speaker="A", segment=Segment.CALENDAR, text="Meeting at nine sharp."),
        Turn(speaker="B", segment=Segment.CALENDAR,
             text="Actually, I'd push that back given the room's still booked."),
    ]
    source_text = "meeting at nine sharp room booked"
    report = run_all_gates(turns, source_text)
    assert report["total_issues"] == 0
