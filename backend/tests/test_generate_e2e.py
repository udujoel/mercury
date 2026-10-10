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

from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from pydub import AudioSegment

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
from app.synthesis.stitch import EmptyBriefingError
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


async def _fake_fetch_articles(*, fixture) -> list[Item]:
    """No news. These tests are about the personal half of the pipeline;
    the news half has its own end-to-end coverage in
    test_news_pipeline_e2e.py, against real RSS fixtures."""
    return []


def _fake_score_emails(
    emails: list[Item], events: list[CalendarEvent] | None = None
) -> list[Item]:
    return sorted(emails, key=lambda i: i.timestamp, reverse=True)


def _fake_select_for_briefing(
    emails: list[Item],
    events: list[CalendarEvent],
    length: BriefingLength,
    *,
    date=None,
) -> dict:
    budget = LENGTH_BUDGETS[length]
    selected_emails = emails[: budget["emails"]]
    return {
        "emails": selected_emails,
        "events": events,
        "aggregated_count": max(0, len(emails) - len(selected_emails)),
    }


async def _fake_generate_script(
    *,
    llm,
    emails: list[Item],
    events: list[CalendarEvent],
    weather,
    length,
    date,
    background_events: list[CalendarEvent] | None = None,
    aggregated_email_count: int = 0,
    follow_up_candidates: list[Item] | None = None,
    stories: list | None = None,
    newsletter_stories: list | None = None,
    newsletter_remainder: int = 0,
    passes: int = 2,
    # Accepted and unused, deliberately: this fake stands in for the real
    # chain's SIGNATURE as much as its output, so a new keyword the pipeline
    # starts passing shows up here as a TypeError rather than silently
    # taking a different path in tests than in production.
    disabled_segments: list[str] | None = None,
    degraded: list[str] | None = None,
    judgment_provider=None,
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
        fetch_articles=_fake_fetch_articles,
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

    # Decode with pydub rather than the `wave` module: the pipeline emits AAC
    # when ffmpeg is installed and WAV when it is not, and this assertion is
    # about manifest-vs-audio agreement, not about the container format.
    fmt = "wav" if "wav" in media_type else "mp4"
    decoded = AudioSegment.from_file(BytesIO(audio_bytes), format=fmt)
    real_duration_ms = len(decoded)

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


@pytest.mark.parametrize(
    ("tz_name", "instant", "local_day"),
    [
        # 23:30Z is 00:30 the next morning in Lisbon — and 2026-03-29 is the
        # night Europe's clocks go forward, so Lisbon is UTC+1 by then.
        ("Europe/Lisbon", datetime(2026, 3, 29, 23, 30, tzinfo=UTC), "2026-03-30"),
        # The other direction: 03:30Z is still the previous evening in LA.
        # A fix that always added a day would pass the Lisbon case alone.
        (
            "America/Los_Angeles",
            datetime(2026, 3, 30, 3, 30, tzinfo=UTC),
            "2026-03-29",
        ),
    ],
)
async def test_default_briefing_id_is_the_users_local_day_not_utcs(
    storage, monkeypatch, tz_name, instant, local_day
):
    """With no `briefing_id`, the id is the date where the user is.

    Every production caller passes an id today, so only this default is at
    stake — but formatting the UTC instant would file a just-after-midnight
    run under yesterday, colliding with yesterday's stored briefing while
    the calendar segment talks about today.
    """
    from app.core.config import get_settings

    monkeypatch.setenv("USER_TIMEZONE", tz_name)
    get_settings.cache_clear()

    manifest = await generate_briefing(
        now=instant,
        length=BriefingLength.STANDARD,
        llm=FakeLLMProvider(),
        tts=FakeTTSProvider(),
        fetch_events=_fake_fetch_events,
        fetch_weather=_fake_fetch_weather,
        score_emails=_fake_score_emails,
        fetch_articles=_fake_fetch_articles,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_fake_generate_script,
        storage=storage,
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    assert manifest.briefing_id == local_day
    stored = storage.load_manifest(local_day)
    assert stored is not None and stored.briefing_id == local_day


async def test_a_naive_now_is_read_as_utc_like_the_ranker_reads_it(
    storage, monkeypatch
):
    """A naive `now` means UTC, the convention `ranking._aware` already uses.

    `datetime.astimezone()` reads a naive value as the HOST's local time,
    so without this the id and the ranker would work from two different
    instants, and which day the id named would depend on the machine. The
    host zone is pinned here so the test means the same thing on a UTC CI
    runner as on a laptop.
    """
    import os
    import time

    from app.core.config import get_settings

    monkeypatch.setenv("USER_TIMEZONE", "Europe/Lisbon")
    get_settings.cache_clear()
    # By hand rather than monkeypatch: time.tzset() has to run AFTER the
    # restore, and monkeypatch only restores at teardown.
    host_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"
    time.tzset()
    try:
        manifest = await generate_briefing(
            # 23:30 UTC is 00:30 on the 30th in Lisbon. Read as Tokyo time it
            # would be 14:30Z, still the 29th in Lisbon.
            now=datetime(2026, 3, 29, 23, 30),
            length=BriefingLength.STANDARD,
            llm=FakeLLMProvider(),
            tts=FakeTTSProvider(),
            fetch_events=_fake_fetch_events,
            fetch_weather=_fake_fetch_weather,
            score_emails=_fake_score_emails,
            fetch_articles=_fake_fetch_articles,
            select_for_briefing=_fake_select_for_briefing,
            generate_script=_fake_generate_script,
            storage=storage,
            gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
            calendar_fixture=None,
            weather_fixture=None,
        )
    finally:
        if host_tz is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = host_tz
        time.tzset()

    assert manifest.briefing_id == "2026-03-30"


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
        fetch_articles=_fake_fetch_articles,
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
        fetch_articles=_fake_fetch_articles,
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
        fetch_articles=_fake_fetch_articles,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_fake_generate_script,
        storage=storage,
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    assert manifest.duration_ms > 0
    assert len(manifest.turns) > 0


async def test_both_personal_sources_failing_is_logged_loudly(caplog, tmp_path):
    """Rule 7 ships the briefing; it should not ship it quietly.

    A real Actions run dropped BOTH email and calendar — the auth demanded a
    credentials.json the runner did not have — and produced a
    weather-and-news briefing that exited 0. A green tick over a briefing
    missing the entire personal half of the product. The degradation is
    correct; the silence was not.
    """

    async def _failing_emails(**kwargs):
        raise RuntimeError("simulated Gmail outage")

    async def _failing_events(**kwargs):
        raise RuntimeError("simulated Calendar outage")

async def test_a_total_tts_failure_persists_nothing(tmp_path):
    """The most damaging shape this pipeline had.

    Every TTS call fails, so there are no audible turns. The old behaviour was
    to store a manifest anyway and exit 0: Actions went green, no failure
    email was sent, and because generation is idempotent that empty briefing
    then blocked its own replacement for the rest of the day. The failure was
    not "no briefing this morning", it was "no briefing today and no way to
    fix it without --force".

    So the assertion that matters is not just that it raises — it is that
    storage is left completely untouched, so tomorrow's run and a plain re-run
    both still work.
    """

    class _TotallyBrokenTTS(FakeTTSProvider):
        async def synthesize(self, text: str, voice_id: str) -> bytes:
            raise RuntimeError("synthetic total TTS failure")

    storage = LocalFilesystemStorage(base_dir=tmp_path)

    with pytest.raises(EmptyBriefingError):
        await generate_briefing(
            briefing_id="2026-08-27",
            length=BriefingLength.STANDARD,
            llm=FakeLLMProvider(),
            tts=_TotallyBrokenTTS(),
            fetch_events=_fake_fetch_events,
            fetch_weather=_fake_fetch_weather,
            score_emails=_fake_score_emails,
            fetch_articles=_fake_fetch_articles,
            select_for_briefing=_fake_select_for_briefing,
            generate_script=_fake_generate_script,
            storage=storage,
            gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
            calendar_fixture=None,
            weather_fixture=None,
        )

    assert storage.load_manifest("2026-08-27") is None
    assert storage.load_audio("2026-08-27") is None
    # Nothing at all, not even a zero-byte audio file that a later run would
    # find and serve.
    assert list(tmp_path.iterdir()) == []


async def test_an_expired_token_survives_the_real_calendar_ingestion(
    tmp_path, monkeypatch
):
    """`app.cli` distinguishes an expired OAuth token from a transient API
    error by exception TYPE, so the type has to survive ingestion.

    It nearly did not: the Gmail and Calendar modules used to re-wrap every
    credential error as a plain RuntimeError, which flattened the one subtype
    that carries a known cause and a permanent fix. Matching on message text
    instead would have broken the first time a string was reworded.

    This drives the REAL `app.ingestion.calendar.fetch_events`, with no
    fixture, down to the real `_google_auth.get_credentials`, and makes the
    stored token fail to refresh with an invalid_grant. The whole chain is
    exercised, so a future re-wrap in `_fetch_live` fails here.

    An earlier version injected a fake that raised ExpiredCredentialsError
    directly. It bypassed both ingestion modules entirely and could only ever
    prove that `_ingest_concurrently` records whatever it was handed — the
    re-wrap it claimed to guard was never executed, and a regression to a
    plain RuntimeError would have passed it. CLAUDE.md names "tests that
    encode the bug" as a recurring class in this project; this was one.
    """
    import sys as _sys
    from pathlib import Path as _Path

    from google.auth.exceptions import RefreshError

    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "scripts"))
    import _google_auth
    from _google_auth import ExpiredCredentialsError

    from app.pipeline.generate import (
        failed_personal_source_errors,
        failed_personal_sources,
    )

    # A token.json that exists and cannot be refreshed — the Actions shape,
    # seven days after the last manual re-mint.
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setenv("GOOGLE_TOKEN_FILE", str(token))
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", str(tmp_path / "absent.json"))

    class _Expired:
        valid = False
        expired = True
        refresh_token = "refresh-me"

        def refresh(self, _request):
            raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(
        _google_auth.Credentials,
        "from_authorized_user_file",
        classmethod(lambda cls, path, scopes: _Expired()),
        raising=True,
    )

    await generate_briefing(
        briefing_id="2026-08-27",
        length=BriefingLength.STANDARD,
        llm=FakeLLMProvider(),
        tts=FakeTTSProvider(),
        # fetch_events deliberately NOT injected: the real ingestion module is
        # the code under test.
        fetch_weather=_fake_fetch_weather,
        score_emails=_fake_score_emails,
        fetch_articles=_fake_fetch_articles,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_fake_generate_script,
        storage=LocalFilesystemStorage(base_dir=tmp_path),
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    # Rule 7: the briefing still shipped, minus the calendar.
    assert failed_personal_sources() == ["calendar"]

    errors = failed_personal_source_errors()
    assert isinstance(errors["calendar"], ExpiredCredentialsError), (
        f"calendar ingestion flattened the type to {type(errors['calendar'])}; "
        "app.cli can no longer tell an expired token from any other failure"
    )


async def test_degraded_sources_survive_a_concurrent_run(storage, monkeypatch):
    """The manifest must record THIS run's failures, not whichever run
    happened to touch the module global last.

    `generate_briefing` spends minutes on LLM and TTS work between ingestion
    and synthesis, and both `app/scheduler/cron.py` and
    `POST /briefing/generate` drive it on the same event loop with no mutual
    gate. Reading `failed_personal_sources()` at the end let a second run
    starting inside that window stamp this manifest with the wrong answer —
    a degraded briefing marked healthy, which is exactly the masking the
    degraded_sources field exists to remove.
    """
    from app.pipeline import generate as gen

    async def _failing_events(**kwargs):
        raise RuntimeError("simulated Calendar outage")

    async def _script_then_interference(**kwargs):
        # Stand in for the minutes of LLM work: while this run is mid-flight,
        # a healthy concurrent run finishes ingestion and clears the global.
        gen._record_failed_sources({})
        return await _fake_generate_script(**kwargs)

    manifest = await generate_briefing(
        briefing_id="2026-08-21",
        length=BriefingLength.STANDARD,
        llm=FakeLLMProvider(),
        tts=FakeTTSProvider(),
        fetch_events=_failing_events,
        fetch_weather=_fake_fetch_weather,
        score_emails=_fake_score_emails,
        select_for_briefing=_fake_select_for_briefing,
        generate_script=_script_then_interference,
        fetch_articles=_fake_fetch_articles,
        storage=storage,
        gmail_fixture=FIXTURES_DIR / "gmail_messages.json",
        calendar_fixture=None,
        weather_fixture=None,
    )

    assert manifest.degraded_sources == ["calendar"], (
        "the manifest recorded the interfering run's state instead of its own"
    )
    # And the global really was clobbered, so the test is testing something.
    assert gen.failed_personal_sources() == []
