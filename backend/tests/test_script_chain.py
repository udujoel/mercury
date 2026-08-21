"""
Tests for app/pipeline/script_chain.py — the Phase 1 two-pass
(outline -> draft) script chain, run end-to-end against FakeLLMProvider.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.schemas import (
    BriefingLength,
    CalendarEvent,
    Item,
    Priority,
    Segment,
    SourceType,
    Turn,
    Weather,
)
from app.pipeline.script_chain import (
    JSONParseError,
    _build_source_refs,
    _parse_draft_turns,
    generate_script,
    parse_json_response,
)
from app.providers.fake import FakeLLMProvider

DATE = datetime(2026, 8, 21, 7, 0, tzinfo=UTC)


def make_email() -> Item:
    return Item(
        source_type=SourceType.EMAIL,
        source_name="Priya Sharma",
        title="Rollout plan sign-off",
        body="Can you review the provider upgrade rollout plan before nine?",
        author="priya@acmecorp.com",
        timestamp=DATE,
        priority=Priority.HIGH,
        requires_response=True,
    )


def make_event() -> CalendarEvent:
    return CalendarEvent(
        id="evt1",
        title="Provider upgrade review",
        start=DATE,
        end=DATE,
    )


def make_weather() -> Weather:
    return Weather(
        temperature_c=16, condition="clear sky", high_c=24, low_c=14,
        location_name="Aveiro",
    )


# ──────────────────────────────────────────────────────────────
# parse_json_response
# ──────────────────────────────────────────────────────────────


def test_parse_json_response_plain_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_parse_json_response_strips_fences():
    text = '```json\n{"a": 1}\n```'
    assert parse_json_response(text) == {"a": 1}


def test_parse_json_response_strips_leading_prose():
    text = 'Here is the JSON you asked for:\n{"a": 1}\nHope that helps!'
    assert parse_json_response(text) == {"a": 1}


def test_parse_json_response_raises_on_garbage():
    with pytest.raises(JSONParseError):
        parse_json_response("not json at all")


def test_parse_json_response_raises_on_none():
    with pytest.raises(JSONParseError):
        parse_json_response(None)


# ──────────────────────────────────────────────────────────────
# turn validation / malformed-turn skipping
# ──────────────────────────────────────────────────────────────


def test_parse_draft_turns_valid():
    raw = [{"speaker": "A", "segment": "cold_open", "text": "Good morning."}]
    turns = _parse_draft_turns(raw)
    assert len(turns) == 1
    assert turns[0].speaker == "A"
    assert turns[0].segment == Segment.COLD_OPEN


def test_parse_draft_turns_skips_invalid_speaker():
    raw = [
        {"speaker": "C", "segment": "cold_open", "text": "Bad speaker"},
        {"speaker": "A", "segment": "cold_open", "text": "Good morning."},
    ]
    turns = _parse_draft_turns(raw)
    assert len(turns) == 1
    assert turns[0].text == "Good morning."


def test_parse_draft_turns_skips_invalid_segment():
    raw = [
        {"speaker": "A", "segment": "not_a_segment", "text": "Bad segment"},
        {"speaker": "B", "segment": "sign_off", "text": "Bye."},
    ]
    turns = _parse_draft_turns(raw)
    assert len(turns) == 1
    assert turns[0].segment == Segment.SIGN_OFF


def test_parse_draft_turns_skips_empty_text():
    raw = [
        {"speaker": "A", "segment": "cold_open", "text": "   "},
        {"speaker": "A", "segment": "cold_open", "text": "Real text"},
    ]
    turns = _parse_draft_turns(raw)
    assert len(turns) == 1


def test_parse_draft_turns_skips_non_dict_entries():
    raw = ["just a string", {"speaker": "A", "segment": "cold_open", "text": "Hi"}]
    turns = _parse_draft_turns(raw)
    assert len(turns) == 1


def test_parse_draft_turns_raises_when_not_a_list():
    with pytest.raises(JSONParseError):
        _parse_draft_turns({"not": "a list"})


# ──────────────────────────────────────────────────────────────
# SourceRef building
# ──────────────────────────────────────────────────────────────


def test_build_source_refs_matches_event_title():
    turns = [Turn(speaker="A", segment=Segment.CALENDAR,
                  text="First up, the Provider upgrade review at nine.")]
    refs = _build_source_refs(turns, [], [make_event()])
    assert len(refs) == 1
    assert refs[0].turn_index == 0
    assert refs[0].source_type == SourceType.CALENDAR
    assert refs[0].title == "Provider upgrade review"


def test_build_source_refs_matches_email_sender():
    turns = [Turn(speaker="A", segment=Segment.INBOX,
                  text="Priya Sharma sent the rollout plan.")]
    refs = _build_source_refs(turns, [make_email()], [])
    assert len(refs) == 1
    assert refs[0].source_type == SourceType.EMAIL
    assert refs[0].source_name == "Priya Sharma"


def test_build_source_refs_no_match_produces_no_refs():
    turns = [Turn(speaker="A", segment=Segment.WEATHER,
                  text="It's a clear morning out there.")]
    refs = _build_source_refs(turns, [make_email()], [make_event()])
    assert refs == []


# ──────────────────────────────────────────────────────────────
# End-to-end chain against FakeLLMProvider
# ──────────────────────────────────────────────────────────────


async def test_generate_script_end_to_end_with_fake_provider():
    llm = FakeLLMProvider()
    turns, source_refs = await generate_script(
        llm=llm,
        emails=[make_email()],
        events=[make_event()],
        weather=make_weather(),
        length=BriefingLength.STANDARD,
        date=DATE,
    )

    assert turns, "expected at least one turn from the draft pass"
    for turn in turns:
        assert isinstance(turn, Turn)
        assert turn.speaker in ("A", "B")

    # Only outline + draft passes run in Phase 1 — critique/revise must
    # NOT be invoked.
    kinds = [kind for kind, _ in llm.calls]
    assert "outline" in kinds
    assert "draft" in kinds
    assert "critique" not in kinds
    assert "revise" not in kinds

    assert isinstance(source_refs, list)


async def test_generate_script_handles_missing_weather_gracefully():
    llm = FakeLLMProvider()
    turns, _ = await generate_script(
        llm=llm,
        emails=[make_email()],
        events=[make_event()],
        weather=None,
        length=BriefingLength.EXPRESS,
        date=DATE,
    )
    assert turns
