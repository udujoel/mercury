"""
End-to-end tests of app.pipeline.generate.generate_briefing.

This is the headline test: real fixtures through the real Gmail ingestion
module, a fake LLM and a fake TTS provider, out the other end an actual
audio file on disk and a manifest whose turns are monotonically increasing
and non-overlapping, whose duration matches the real audio length, and
whose segment offsets point at the first turn of each segment.

app.ingestion.calendar, app.ingestion.weather, app.pipeline.scoring and
app.pipeline.script_chain are owned by another agent working in parallel
and may not exist yet, so this test injects small fakes that satisfy the
interface contract documented in app/pipeline/generate.py exactly. This
keeps the suite runnable today; nothing here reimplements or shadows those
modules on disk.
"""

from __future__ import annotations

import wave
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest

from app.core.schemas import (
    LENGTH_BUDGETS,
    BriefingLength,
    CalendarEvent,
    Item,
    Segment,
    SourceRef,
    SourceType,
    Turn,
)
from app.pipeline.generate import generate_briefing
from app.providers.fake import FakeLLMProvider
from app.providers.fake_tts import FakeTTSProvider
from app.synthesis.storage import LocalFilesystemStorage

FIXTURES_DIR = Path(__file__).parent / "fixtures"


async def _fake_fetch_events(*, fixture, hours_ahead: int) -> list[CalendarEvent]:
    return [
        CalendarEvent(
            id="ev1",
            title="Provider upgrade review",
            start=datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
            end=datetime(2026, 8, 21, 9, 30, tzinfo=UTC),
            attendees=["priya@example.com"],
        )
    ]


async def _failing_fetch_events(*, fixture, hours_ahead: int) -> list[CalendarEvent]:
    raise RuntimeError("synthetic calendar outage")


async def _fake_fetch_weather(*, lat: float, lon: float, fixture):
    return None  # weather is optional; None must not break the run


def _fake_score_emails(
    emails: list[Item], events: list[CalendarEvent] | None = None
) -> list[Item]:
    return sorted(emails, key=lambda i: i.timestamp, reverse=True)


def _fake_select_for_briefing(
    emails: list[Item], events: list[CalendarEvent], length: BriefingLength
) -> dict:
    budget = LENGTH_BUDGETS[length]
    selected_emails = emails[: budget["emails"]]
    return {
        "emails": selected_emails,
        "events": events,
        "aggregated_count": max(0, len(emails) - len(selected_emails)),
    }


async def _fake_generate_script(
    *, llm, emails: list[Item], events: list[CalendarEvent], weather, length, date
) -> tuple[list[Turn], list[SourceRef]]:
    # Exercise the injected LLM (as the real script chain would) without
    # depending on its exact prompt-dispatch behaviour.
    await llm.complete("system", "Produce an OUTLINE for the briefing test")

    turns = [
        Turn(speaker="A", segment=Segment.COLD_OPEN, text="Good morning Joel."),
        Turn(speaker="B", segment=Segment.COLD_OPEN, text="Let's get into your day."),
    ]
    sources: list[SourceRef] = []

    if events:
        turns.append(
            Turn(
                speaker="A", segment=Segment.CALENDAR,
                text=f"First up, {events[0].title} this morning.",
            )
        )
        sources.append(
            SourceRef(
                turn_index=len(turns) - 1, title=events[0].title, url=None,
                source_name="Calendar", source_type=SourceType.CALENDAR,
            )
        )

    for item in emails[:2]:
        turns.append(
            Turn(
                speaker="B", segment=Segment.INBOX,
                text=f"From {item.source_name}: {item.title}.",
            )
        )
        sources.append(
            SourceRef(
                turn_index=len(turns) - 1, title=item.title, url=item.url,
                source_name=item.source_name, source_type=SourceType.EMAIL,
            )
        )

    turns.append(
        Turn(speaker="A", segment=Segment.SIGN_OFF, text="That's the morning.")
    )
    return turns, sources


@pytest.fixture
def storage(tmp_path):
    return LocalFilesystemStorage(base_dir=tmp_path)


async def test_generate_briefing_end_to_end_produces_audio_and_valid_manifest(storage):
    manifest = await generate_briefing(
        briefing_id="2026-08-21",
        length=BriefingLength.STANDARD,
        llm=FakeLLMProvider(),
        tts=FakeTTSProvider(),
        fetch_events=_fake_fetch_events,
        fetch_weather=_fake_fetch_weather,
        score_emails=_fake_score_emails,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_fake_generate_script,
        storage=storage,
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    # A real audio file landed on disk.
    audio_result = storage.load_audio("2026-08-21")
    assert audio_result is not None
    audio_bytes, media_type = audio_result
    assert len(audio_bytes) > 0

    with wave.open(BytesIO(audio_bytes), "rb") as wf:
        real_duration_ms = round(1000 * wf.getnframes() / wf.getframerate())

    # duration_ms in the manifest matches the real audio length.
    assert manifest.duration_ms == real_duration_ms

    # turns: monotonically increasing, non-overlapping.
    prev_end = -1
    for turn in manifest.turns:
        assert turn.start_ms is not None and turn.end_ms is not None
        assert turn.start_ms >= prev_end
        assert turn.end_ms > turn.start_ms
        prev_end = turn.end_ms

    # segments: first turn of each segment.
    turns_by_index = manifest.turns
    for seg in manifest.segments:
        first_turn = next(t for t in turns_by_index if t.segment.value == seg["id"])
        assert seg["start_ms"] == first_turn.start_ms

    # sources reference real turn indices.
    for ref in manifest.sources:
        assert 0 <= ref.turn_index < len(manifest.turns)

    # The manifest round-trips from storage identically.
    stored_manifest = storage.load_manifest("2026-08-21")
    assert stored_manifest is not None
    assert stored_manifest.briefing_id == manifest.briefing_id
    assert stored_manifest.duration_ms == manifest.duration_ms


async def test_generate_briefing_drops_raw_email_bodies_before_persisting(storage):
    from app.ingestion.gmail import fetch_emails as real_fetch_emails

    captured: list[Item] = []

    async def _capturing_fetch_emails(*, fixture, max_messages):
        items = await real_fetch_emails(fixture=fixture, max_messages=max_messages)
        captured.extend(items)
        assert any(i.body for i in items), (
            "fixture produced no email bodies to test scrubbing against"
        )
        return items

    manifest = await generate_briefing(
        briefing_id="2026-08-21",
        length=BriefingLength.STANDARD,
        llm=FakeLLMProvider(),
        tts=FakeTTSProvider(),
        fetch_emails=_capturing_fetch_emails,
        fetch_events=_fake_fetch_events,
        fetch_weather=_fake_fetch_weather,
        score_emails=_fake_score_emails,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_fake_generate_script,
        storage=storage,
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    # The actual behaviour under test: every Item this run touched has had
    # its raw body cleared once the script chain no longer needs it —
    # CLAUDE.md rule 5, raw bodies are ephemeral to the run.
    assert captured, "no emails were ingested to test scrubbing against"
    assert all(item.body == "" for item in captured)

    # CLAUDE.md rule 5: BriefingManifest has no `body` field at all — Turn
    # and SourceRef only ever carry generated dialogue text and metadata,
    # so a raw email body has no field to smuggle through into what gets
    # persisted. Assert that structurally against the schema itself.
    assert "body" not in Turn.model_fields
    assert "body" not in SourceRef.model_fields
    assert "body" not in type(manifest).model_fields


async def test_generate_briefing_degrades_gracefully_when_a_source_fails(storage):
    """CLAUDE.md rule 7: one source failing must not abort the run."""
    manifest = await generate_briefing(
        briefing_id="2026-08-21",
        length=BriefingLength.STANDARD,
        llm=FakeLLMProvider(),
        tts=FakeTTSProvider(),
        fetch_events=_failing_fetch_events,  # <- deliberately broken
        fetch_weather=_fake_fetch_weather,
        score_emails=_fake_score_emails,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_fake_generate_script,
        storage=storage,
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    # The briefing still shipped.
    assert manifest.duration_ms > 0
    assert len(manifest.turns) > 0
    # ...just without a calendar segment, since fetch_events failed and
    # _fake_generate_script only adds one when events is non-empty.
    segment_ids = {s["id"] for s in manifest.segments}
    assert Segment.CALENDAR.value not in segment_ids
    assert Segment.COLD_OPEN.value in segment_ids


async def test_generate_briefing_degrades_when_every_optional_source_fails(storage):
    async def _failing_fetch_weather(*, lat, lon, fixture):
        raise RuntimeError("synthetic weather outage")

    manifest = await generate_briefing(
        briefing_id="2026-08-21",
        length=BriefingLength.STANDARD,
        llm=FakeLLMProvider(),
        tts=FakeTTSProvider(),
        fetch_events=_failing_fetch_events,
        fetch_weather=_failing_fetch_weather,
        score_emails=_fake_score_emails,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_fake_generate_script,
        storage=storage,
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    assert manifest.duration_ms > 0
    assert len(manifest.turns) > 0
