#!/usr/bin/env python3
"""
Phase 0 Spike 3 — Calendar fetch, today plus 72 hours, across ALL calendars.

Enumerates every calendar on the account (calendarList.list), then fetches
events on each (events.list, singleEvents=True so recurring events are
already expanded into concrete instances) within [now, now+72h]. Normalises
into `CalendarEvent` and prints a chronological, day-grouped view.

Throwaway spike code (CLAUDE.md, docs/ROADMAP.md Phase 0) — written so the
timezone/all-day/recurrence/declined handling is easy to lift into
backend/app/ingestion/calendar.py in Phase 1, but that module does not exist
yet and this script must not import from backend/app.

Usage:
    python scripts/spike_03_calendar.py --fixture [--json]
    python scripts/spike_03_calendar.py --live [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from _google_auth import credentials_available, get_credentials  # noqa: E402
from app.core.schemas import CalendarEvent  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "backend/tests/fixtures/calendar_events.json"

USER_TIMEZONE = "Europe/Lisbon"
WINDOW_HOURS = 72

# ──────────────────────────────────────────────────────────────
# Video-conference boilerplate stripping
# ──────────────────────────────────────────────────────────────

JOIN_URL_RE = re.compile(
    r"https://(meet\.google\.com|zoom\.us|teams\.microsoft\.com)\S*"
)
BOILERPLATE_LINE_RE = re.compile(
    r"^.*(join with google meet|join zoom meeting|join microsoft teams|"
    r"or dial|meeting id|passcode|one tap mobile)\b.*$",
    re.IGNORECASE | re.MULTILINE,
)

PREP_KEYWORDS = ["agenda", "please review", "prep", "bring", "read ahead", "prepare"]
DOC_LINK_RE = re.compile(r"https://docs\.google\.com/\S+")


def extract_join_url(
    description: str, conference_data: dict[str, Any] | None
) -> str | None:
    if conference_data:
        for entry in conference_data.get("entryPoints", []):
            if entry.get("entryPointType") == "video" and entry.get("uri"):
                return entry["uri"]
    match = JOIN_URL_RE.search(description or "")
    return match.group(0) if match else None


def strip_conference_boilerplate(description: str) -> str:
    text = BOILERPLATE_LINE_RE.sub("", description)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def needs_prep(description: str, attendee_count: int) -> bool:
    """True if the event has an agenda/doc link, 3+ attendees, or the
    description contains prep-signalling language. Any one signal is
    enough — meetings rarely need more than one reason to warrant prep."""
    if DOC_LINK_RE.search(description or ""):
        return True
    if attendee_count >= 3:
        return True
    lowered = (description or "").lower()
    return any(keyword in lowered for keyword in PREP_KEYWORDS)


# ──────────────────────────────────────────────────────────────
# Time handling
# ──────────────────────────────────────────────────────────────


def parse_event_time(time_obj: dict[str, str], tz: ZoneInfo) -> tuple[datetime, bool]:
    """Returns (datetime in USER_TIMEZONE, is_all_day)."""
    if "date" in time_obj:
        d = date.fromisoformat(time_obj["date"])
        return datetime(d.year, d.month, d.day, tzinfo=tz), True
    dt = datetime.fromisoformat(time_obj["dateTime"])
    return dt.astimezone(tz), False


# ──────────────────────────────────────────────────────────────
# Normalisation
# ──────────────────────────────────────────────────────────────


def extract_attendees(raw_attendees: list[dict[str, Any]]) -> list[str]:
    out = []
    for a in raw_attendees:
        if a.get("self"):
            continue  # the user themself isn't a useful "attendee" to display
        out.append(a.get("displayName") or a.get("email", ""))
    return [a for a in out if a]


def user_declined(raw_attendees: list[dict[str, Any]]) -> bool:
    return any(
        a.get("self") and a.get("responseStatus") == "declined"
        for a in raw_attendees
    )


def event_to_calendar_event(raw: dict[str, Any], tz: ZoneInfo) -> CalendarEvent | None:
    if raw.get("status") == "cancelled":
        return None

    attendees_raw = raw.get("attendees", [])
    if user_declined(attendees_raw):
        return None

    start_raw = raw.get("start")
    end_raw = raw.get("end")
    if not start_raw or not end_raw:
        raise ValueError(f"event {raw.get('id')} missing start/end")

    start, _ = parse_event_time(start_raw, tz)
    end, _ = parse_event_time(end_raw, tz)

    description = raw.get("description", "") or ""
    conference_data = raw.get("conferenceData")
    join_url = extract_join_url(description, conference_data)
    clean_description = strip_conference_boilerplate(description)
    if join_url and join_url not in clean_description:
        clean_description = (clean_description + f"\n\nJoin: {join_url}").strip()

    attendee_names = extract_attendees(attendees_raw)

    return CalendarEvent(
        id=raw["id"],
        title=raw.get("summary", "(no title)"),
        start=start,
        end=end,
        location=raw.get("location"),
        description=clean_description or None,
        attendees=attendee_names,
        needs_prep=needs_prep(description, len(attendee_names) + 1),
    )


# ──────────────────────────────────────────────────────────────
# Fetch paths
# ──────────────────────────────────────────────────────────────


def load_fixture() -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    data = json.loads(FIXTURE_PATH.read_text())
    calendars = data["calendarList"]["items"]
    events_by_cal = {
        cal_id: payload["items"] for cal_id, payload in data["eventsByCalendar"].items()
    }
    return calendars, events_by_cal


def fetch_live(
    time_min: datetime, time_max: datetime
) -> tuple[list[dict], dict[str, list[dict]]]:
    from googleapiclient.discovery import build

    creds = get_credentials()
    service = build("calendar", "v3", credentials=creds)

    calendars = service.calendarList().list().execute().get("items", [])

    events_by_cal: dict[str, list[dict]] = {}
    for cal in calendars:
        cal_id = cal["id"]
        events: list[dict] = []
        page_token = None
        while True:
            resp = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    pageToken=page_token,
                )
                .execute()
            )
            events.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        events_by_cal[cal_id] = events

    return calendars, events_by_cal


# ──────────────────────────────────────────────────────────────
# Output
# ──────────────────────────────────────────────────────────────


def print_day_grouped(events: list[CalendarEvent]) -> None:
    events = sorted(events, key=lambda e: e.start)
    current_day = None
    for e in events:
        day = e.start.date()
        if day != current_day:
            current_day = day
            print(f"\n== {day.isoformat()} ({day.strftime('%A')}) ==")
        spans_full_day = e.end - e.start >= timedelta(hours=23)
        is_all_day = e.start.time() == time(0, 0) and spans_full_day
        time_str = "all day" if is_all_day else e.start.strftime("%H:%M")
        prep = " [needs prep]" if e.needs_prep else ""
        attendee_str = f" — {len(e.attendees)} attendee(s)" if e.attendees else ""
        print(f"  {time_str}  {e.title}{attendee_str}{prep}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike 3 — Calendar fetch")
    parser.add_argument("--fixture", action="store_true", help="use offline fixture")
    parser.add_argument("--live", action="store_true", help="hit the real Calendar API")
    parser.add_argument("--json", action="store_true", help="dump normalised events")
    parser.add_argument("--tz", default=USER_TIMEZONE)
    args = parser.parse_args()

    tz = ZoneInfo(args.tz)

    use_live = args.live and not args.fixture
    if not args.live and not args.fixture:
        use_live = credentials_available()

    now = datetime.now(tz)
    window_end = now + timedelta(hours=WINDOW_HOURS)

    if use_live:
        print(
            f"[live] fetching events {now.isoformat()} .. {window_end.isoformat()}",
            file=sys.stderr,
        )
        try:
            calendars, events_by_cal = fetch_live(now, window_end)
        except Exception as exc:
            print(f"live fetch failed: {exc}", file=sys.stderr)
            return 1
    else:
        print(f"[fixture] reading {FIXTURE_PATH}", file=sys.stderr)
        calendars, events_by_cal = load_fixture()

    cal_names = [c["summary"] for c in calendars]
    print(f"{len(calendars)} calendars found: {cal_names}", file=sys.stderr)

    all_events: list[CalendarEvent] = []
    skipped = 0
    for cal in calendars:
        cal_id = cal["id"]
        for raw_event in events_by_cal.get(cal_id, []):
            try:
                event = event_to_calendar_event(raw_event, tz)
            except Exception as exc:
                skipped += 1
                print(
                    f"skipping malformed event {raw_event.get('id', '?')}: {exc}",
                    file=sys.stderr,
                )
                continue
            if event is not None:
                all_events.append(event)

    if args.json:
        payload = [json.loads(e.model_dump_json()) for e in all_events]
        print(json.dumps(payload, indent=2))
    else:
        print_day_grouped(all_events)
        summary = (
            f"\n{len(all_events)} events across {len(calendars)} calendars, "
            f"{skipped} skipped."
        )
        print(summary)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
