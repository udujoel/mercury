"""
Offline tests for Spike 4 (Gemini script chain), using FakeLLMProvider —
no network calls.

Covers: the chain runs end to end; parse_json_response survives the failure
shapes LLMs actually produce (fenced, trailing-prose, array-vs-object);
turns validate against the shared `Turn` schema; segment order and coverage;
each quality-gate function correctly flags a known-bad sample and passes a
known-good one; and revision actually removes the two planted defects.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import spike_04_llm as spike  # noqa: E402

from app.core.schemas import Segment, Turn  # noqa: E402
from app.providers.fake import FakeLLMProvider  # noqa: E402

# ──────────────────────────────────────────────────────────────
# End-to-end chain
# ──────────────────────────────────────────────────────────────


@pytest.fixture
def fixture_data():
    return spike.load_fixture()


async def test_chain_runs_end_to_end(fixture_data):
    provider = FakeLLMProvider()
    result = await spike.run_chain(provider, fixture_data)

    assert result["outline"]["segments"]
    assert result["draft_turns"]
    assert result["revised_turns"]
    # all four passes were actually invoked
    kinds = [k for k, _ in provider.calls]
    assert kinds == ["outline", "draft", "critique", "revise"]


async def test_revised_turns_validate_and_cover_all_segments(fixture_data):
    provider = FakeLLMProvider()
    result = await spike.run_chain(provider, fixture_data)
    turns = result["revised_turns"]

    assert all(isinstance(t, Turn) for t in turns)
    assert all(t.speaker in ("A", "B") for t in turns)
    spike.validate_segment_coverage(turns)  # raises on failure


async def test_word_count_within_budget(fixture_data):
    provider = FakeLLMProvider()
    result = await spike.run_chain(provider, fixture_data)
    words = spike.word_count(result["revised_turns"])
    budget = result["word_budget"]
    assert budget * 0.8 <= words <= budget * 1.2


async def test_revision_removes_planted_defects(fixture_data):
    provider = FakeLLMProvider()
    result = await spike.run_chain(provider, fixture_data)

    draft_monologues = spike.detect_monologues(result["draft_turns"])
    revised_monologues = spike.detect_monologues(result["revised_turns"])
    assert draft_monologues, "fixture draft should contain the planted monologue"
    assert not revised_monologues, "revision should have split the monologue"

    draft_gaps = spike.detect_missing_disagreement(result["draft_turns"])
    revised_gaps = spike.detect_missing_disagreement(result["revised_turns"])
    draft_calendar_flagged = any(
        g["segment"] == "calendar"
        for g in draft_gaps["segments_missing_disagreement"]
    )
    revised_calendar_flagged = any(
        g["segment"] == "calendar"
        for g in revised_gaps["segments_missing_disagreement"]
    )
    assert draft_calendar_flagged, "fixture draft's calendar segment should be flagged"
    assert not revised_calendar_flagged, "revision should add a challenge to calendar"


# ──────────────────────────────────────────────────────────────
# parse_json_response — the riskiest part
# ──────────────────────────────────────────────────────────────


def test_parse_plain_json_object():
    assert spike.parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_plain_json_array():
    assert spike.parse_json_response('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]


def test_parse_fenced_json():
    text = '```json\n{"a": 1}\n```'
    assert spike.parse_json_response(text) == {"a": 1}


def test_parse_fenced_json_no_language_tag():
    text = '```\n{"a": 1}\n```'
    assert spike.parse_json_response(text) == {"a": 1}


def test_parse_trailing_prose():
    text = (
        'Here is the JSON you asked for:\n{"a": 1}\n'
        "Let me know if you need anything else!"
    )
    assert spike.parse_json_response(text) == {"a": 1}


def test_parse_leading_and_trailing_prose_array():
    text = 'Sure thing:\n[{"speaker": "A", "text": "hi"}]\nHope that helps.'
    result = spike.parse_json_response(text)
    assert result == [{"speaker": "A", "text": "hi"}]


def test_parse_smart_quotes():
    # Some models emit smart/curly quotes as the JSON structural delimiters
    # themselves (invalid JSON) instead of straight quotes.
    text = "{“a”: “hello”}"
    result = spike.parse_json_response(text)
    assert result == {"a": "hello"}


def test_parse_nested_brackets_in_strings():
    text = '{"a": "list is [1, 2] and dict is {x: y}", "b": 2}'
    result = spike.parse_json_response(text)
    assert result["a"] == "list is [1, 2] and dict is {x: y}"
    assert result["b"] == 2


def test_parse_raises_useful_error_on_garbage():
    with pytest.raises(spike.JSONParseError) as exc_info:
        spike.parse_json_response("I'm sorry, I can't help with that.")
    assert "raw response" in str(exc_info.value)


def test_parse_raises_on_none():
    with pytest.raises(spike.JSONParseError):
        spike.parse_json_response(None)


# ──────────────────────────────────────────────────────────────
# validate_turns / validate_segment_coverage
# ──────────────────────────────────────────────────────────────


def test_validate_turns_accepts_valid_array():
    raw = [
        {"speaker": "A", "segment": "cold_open", "text": "Good morning."},
        {"speaker": "B", "segment": "cold_open", "text": "Hey there."},
    ]
    turns = spike.validate_turns(raw)
    assert len(turns) == 2
    assert turns[0].segment == Segment.COLD_OPEN


def test_validate_turns_rejects_bad_speaker():
    raw = [{"speaker": "C", "segment": "cold_open", "text": "hi"}]
    with pytest.raises(ValueError):
        spike.validate_turns(raw)


def test_validate_turns_rejects_non_list():
    with pytest.raises(ValueError):
        spike.validate_turns({"speaker": "A"})


def test_validate_segment_coverage_detects_missing_segment():
    turns = [Turn(speaker="A", segment=Segment.COLD_OPEN, text="hi")]
    with pytest.raises(ValueError, match="missing segments"):
        spike.validate_segment_coverage(turns)


def test_validate_segment_coverage_detects_out_of_order():
    turns = [
        Turn(speaker="A", segment=s, text="x")
        for s in [
            Segment.WEATHER, Segment.COLD_OPEN, Segment.CALENDAR,
            Segment.INBOX, Segment.WORLD, Segment.FOLLOW_UPS, Segment.SIGN_OFF,
        ]
    ]
    with pytest.raises(ValueError, match="out of canonical order"):
        spike.validate_segment_coverage(turns)


def test_validate_segment_coverage_accepts_canonical_order():
    turns = [
        Turn(speaker="A", segment=s, text="x")
        for s in spike.CANONICAL_SEGMENT_ORDER
    ]
    spike.validate_segment_coverage(turns)  # should not raise


# ──────────────────────────────────────────────────────────────
# Quality gate functions — known-bad and known-good samples
# ──────────────────────────────────────────────────────────────


def test_detect_filler_words_flags_known_bad():
    turns = [Turn(speaker="A", segment=Segment.COLD_OPEN, text="Um, good morning.")]
    hits = spike.detect_filler_words(turns)
    assert len(hits) == 1
    assert hits[0]["word"] == "um"


def test_detect_filler_words_passes_known_good():
    turns = [Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning, Joel.")]
    assert spike.detect_filler_words(turns) == []


def test_detect_filler_words_no_false_positive_on_substring():
    # "um" is a substring of "bump" and "album" — must not false-positive.
    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN,
             text="The lxml version bump landed, and so did the new album."),
    ]
    assert spike.detect_filler_words(turns) == []


def test_detect_monologues_flags_known_bad():
    text = "One. Two. Three. Four. Five."
    turns = [Turn(speaker="A", segment=Segment.WORLD, text=text)]
    hits = spike.detect_monologues(turns)
    assert len(hits) == 1
    assert hits[0]["sentence_count"] == 5


def test_detect_monologues_passes_known_good():
    text = "One. Two. Three. Four."
    turns = [Turn(speaker="A", segment=Segment.WORLD, text=text)]
    assert spike.detect_monologues(turns) == []


def test_detect_missing_disagreement_flags_known_bad():
    turns = [
        Turn(speaker="A", segment=Segment.CALENDAR, text="Meeting at nine."),
        Turn(speaker="B", segment=Segment.CALENDAR, text="Sounds good, agreed."),
    ]
    result = spike.detect_missing_disagreement(turns)
    segments = [g["segment"] for g in result["segments_missing_disagreement"]]
    assert "calendar" in segments


def test_detect_missing_disagreement_passes_known_good():
    turns = [
        Turn(speaker="A", segment=Segment.CALENDAR, text="Meeting at nine."),
        Turn(speaker="B", segment=Segment.CALENDAR,
             text="Actually, I'd push back on moving it that early."),
    ]
    result = spike.detect_missing_disagreement(turns)
    segments = [g["segment"] for g in result["segments_missing_disagreement"]]
    assert "calendar" not in segments


def test_check_grounding_flags_known_bad():
    turns = [
        Turn(speaker="A", segment=Segment.INBOX,
             text="Zorbex acquired Fintastic for 40 million dollars."),
    ]
    source_text = '{"title": "an unrelated email about lunch"}'
    hits = spike.check_grounding(turns, source_text)
    tokens = {h["token"] for h in hits}
    assert "Zorbex" in tokens
    assert "Fintastic" in tokens


def test_check_grounding_passes_known_good():
    turns = [
        Turn(speaker="A", segment=Segment.INBOX,
             text="Priya sent the rollout plan."),
    ]
    source_text = '{"author": "priya@platform.mercury.dev", "title": "rollout plan"}'
    hits = spike.check_grounding(turns, source_text)
    assert hits == []
