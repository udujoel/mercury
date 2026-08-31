"""
A source that silently loses its input must not look like a quiet source.

Both Gmail and Calendar normalise per item inside a bare `except: continue`.
That is right under CLAUDE.md rule 7 — one malformed message must not take
down the run — but it was written with no log, no counter and no exception
detail, so a run where every message failed to normalise produced an empty
list, no ERROR, no `::warning::` and a green tick. Indistinguishable from a
morning with no email in it.

These tests pin the three things that make it distinguishable: every drop is
logged with its id and its traceback, the count is reported, and a source
that lost most of a non-empty input is escalated so
`app.pipeline.generate._record_failed_sources` picks it up and `app/cli.py`
prints the warning the runner sees.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.ingestion import calendar as calendar_mod
from app.ingestion import gmail as gmail_mod
from app.ingestion.drops import SourceNormalisationError

FIXTURES = Path(__file__).parent / "fixtures"


def _gmail_fixture(tmp_path: Path, messages: list[dict]) -> Path:
    path = tmp_path / "gmail.json"
    path.write_text(json.dumps({"messages": messages}))
    return path


def _good_message(msg_id: str) -> dict:
    return {
        "id": msg_id,
        "threadId": f"t{msg_id}",
        "internalDate": "1755706000000",
        "payload": {
            "headers": [
                {"name": "From", "value": "Maria Chen <maria@acmecorp.com>"},
                {"name": "Subject", "value": "Budget"},
                {"name": "To", "value": "joel@example.com"},
            ],
            "body": {},
            "parts": [],
        },
    }


def _broken_message(msg_id: str) -> dict:
    # No payload at all — `message_to_item` raises ValueError on this, which
    # is the real shape of a normalisation failure rather than a synthetic
    # monkeypatched one.
    return {"id": msg_id, "threadId": f"t{msg_id}", "internalDate": "1755706000000"}


def _calendar_fixture(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "calendar.json"
    path.write_text(
        json.dumps(
            {
                "calendarList": {"items": [{"id": "cal1", "accessRole": "owner"}]},
                "eventsByCalendar": {"cal1": {"items": events}},
            }
        )
    )
    return path


def _good_event(event_id: str) -> dict:
    return {
        "id": event_id,
        "summary": "Standup",
        "start": {"dateTime": "2026-08-21T09:30:00+01:00"},
        "end": {"dateTime": "2026-08-21T09:45:00+01:00"},
    }


def _broken_event(event_id: str) -> dict:
    # Missing start/end — `event_to_calendar_event` raises ValueError.
    return {"id": event_id, "summary": "Broken"}


# ──────────────────────────────────────────────────────────────
# Every drop is visible
# ──────────────────────────────────────────────────────────────


async def test_gmail_logs_each_dropped_message_with_id_and_traceback(
    tmp_path, caplog
):
    fixture = _gmail_fixture(
        tmp_path, [_good_message("ok1"), _good_message("ok2"), _broken_message("bad1")]
    )
    with caplog.at_level(logging.WARNING):
        items = await gmail_mod.fetch_emails(fixture=fixture)

    assert len(items) == 2

    drop_records = [r for r in caplog.records if "bad1" in r.getMessage()]
    assert drop_records, "the dropped message id must appear in the log"
    assert drop_records[0].levelno == logging.WARNING
    # exc_info=True: without the traceback the operator knows a message was
    # lost but not why, which is barely better than silence.
    assert drop_records[0].exc_info is not None


async def test_gmail_reports_the_drop_count(tmp_path, caplog):
    fixture = _gmail_fixture(
        tmp_path, [_good_message(f"ok{i}") for i in range(6)] + [_broken_message("bad")]
    )
    with caplog.at_level(logging.WARNING):
        await gmail_mod.fetch_emails(fixture=fixture)

    summaries = [
        r.getMessage()
        for r in caplog.records
        if "failed to normalise" in r.getMessage()
    ]
    assert any("1 of 7" in m for m in summaries), summaries


async def test_calendar_logs_each_dropped_event_with_id_and_traceback(
    tmp_path, caplog
):
    fixture = _calendar_fixture(
        tmp_path, [_good_event("ok1"), _good_event("ok2"), _broken_event("bad1")]
    )
    with caplog.at_level(logging.WARNING):
        events = await calendar_mod.fetch_events(fixture=fixture)

    assert len(events) == 2

    drop_records = [r for r in caplog.records if "bad1" in r.getMessage()]
    assert drop_records, "the dropped event id must appear in the log"
    assert drop_records[0].levelno == logging.WARNING
    assert drop_records[0].exc_info is not None


async def test_cancelled_and_declined_events_are_not_counted_as_drops(
    tmp_path, caplog
):
    """A filtered-out event is not a failure. `event_to_calendar_event`
    returns None for cancelled events and for ones the user declined, and
    counting those as drops would fire the escalation on a perfectly healthy
    calendar."""
    cancelled = {**_good_event("cancelled1"), "status": "cancelled"}
    declined = {
        **_good_event("declined1"),
        "attendees": [{"self": True, "responseStatus": "declined"}],
    }
    fixture = _calendar_fixture(tmp_path, [_good_event("ok1"), cancelled, declined])

    with caplog.at_level(logging.WARNING):
        events = await calendar_mod.fetch_events(fixture=fixture)

    assert len(events) == 1
    assert not [
        r for r in caplog.records if "failed to normalise" in r.getMessage()
    ]


# ──────────────────────────────────────────────────────────────
# Escalation when most of a non-empty input is lost
# ──────────────────────────────────────────────────────────────


async def test_gmail_raises_when_every_message_fails_to_normalise(tmp_path):
    """The headline case: 60 of 60 dropped used to return `[]` and exit 0.

    Raising is what puts it into `generate._failed_personal_sources`, which
    is what makes `app/cli.py` print the `::warning::`. The briefing still
    ships without the segment — `_ingest_concurrently` catches this — so
    rule 7 still holds.
    """
    fixture = _gmail_fixture(tmp_path, [_broken_message(f"bad{i}") for i in range(60)])
    with pytest.raises(SourceNormalisationError) as excinfo:
        await gmail_mod.fetch_emails(fixture=fixture)
    assert "60" in str(excinfo.value)


async def test_calendar_raises_when_every_event_fails_to_normalise(tmp_path):
    fixture = _calendar_fixture(tmp_path, [_broken_event(f"bad{i}") for i in range(10)])
    with pytest.raises(SourceNormalisationError):
        await calendar_mod.fetch_events(fixture=fixture)


async def test_a_handful_of_drops_does_not_raise(tmp_path, caplog):
    """Losing a couple of messages out of a healthy inbox is what rule 7 is
    for. It gets logged; it does not cost the user their inbox segment."""
    messages = [_good_message(f"ok{i}") for i in range(10)] + [
        _broken_message("bad1"),
        _broken_message("bad2"),
    ]
    with caplog.at_level(logging.WARNING):
        items = await gmail_mod.fetch_emails(fixture=_gmail_fixture(tmp_path, messages))
    assert len(items) == 10


async def test_one_drop_out_of_two_does_not_raise(tmp_path):
    """Ratio alone is not enough to call a source broken. One bad message on
    a two-message morning is 50% and means nothing."""
    fixture = _gmail_fixture(tmp_path, [_good_message("ok1"), _broken_message("bad1")])
    items = await gmail_mod.fetch_emails(fixture=fixture)
    assert len(items) == 1


async def test_three_drops_out_of_thirteen_does_not_raise(tmp_path):
    """The other bar, pinned in the other direction.

    Every existing "does not raise" case here drops fewer than
    ESCALATION_MIN_DROPPED, so `report_drops` short-circuits on the absolute
    floor and never reaches the ratio at all — delete ESCALATION_RATIO and
    they all still pass. This one clears the floor (3) and stays well under
    the ratio (3/13), so it fails if the ratio bar is removed and both bars
    are now load-bearing.
    """
    messages = [_good_message(f"ok{i}") for i in range(10)] + [
        _broken_message(f"bad{i}") for i in range(3)
    ]
    items = await gmail_mod.fetch_emails(fixture=_gmail_fixture(tmp_path, messages))
    assert len(items) == 10


async def test_the_shipped_gmail_fixture_still_normalises(tmp_path):
    """Guard against the escalation firing on the repo's own fixture, which
    deliberately carries one payload-less message."""
    items = await gmail_mod.fetch_emails(fixture=FIXTURES / "gmail_messages.json")
    assert len(items) == 4


# ──────────────────────────────────────────────────────────────
# The handler itself must not be the thing that takes the run down
# ──────────────────────────────────────────────────────────────


async def test_a_non_mapping_message_is_dropped_like_any_other(tmp_path):
    """The `except` block was reaching into the item it had just failed on.

    `raw.get("id")` assumes the thing that would not normalise is at least a
    dict. A `null` in the message list is not, so the AttributeError escaped
    the handler and aborted `fetch_emails` — the exact whole-run failure the
    handler exists to prevent, triggered by the handler.
    """
    fixture = _gmail_fixture(
        tmp_path, [_good_message("ok1"), None, "not-a-message", _good_message("ok2")]
    )
    items = await gmail_mod.fetch_emails(fixture=fixture)
    assert len(items) == 2


async def test_a_non_mapping_event_is_dropped_like_any_other(tmp_path):
    fixture = _calendar_fixture(
        tmp_path, [_good_event("ok1"), None, _good_event("ok2")]
    )
    events = await calendar_mod.fetch_events(fixture=fixture)
    assert len(events) == 2


async def test_the_drop_log_says_what_shape_arrived_instead(tmp_path, caplog):
    """A logged id of `None` is indistinguishable from a dict whose id key is
    missing. The operator needs to know the payload shape itself was wrong."""
    fixture = _gmail_fixture(tmp_path, [_good_message("ok1"), None])
    with caplog.at_level(logging.WARNING):
        await gmail_mod.fetch_emails(fixture=fixture)
    assert any("non-mapping NoneType" in rec.getMessage() for rec in caplog.records)
