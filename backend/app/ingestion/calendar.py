"""
Calendar ingestion.

Ports the timezone/all-day/recurrence/declined-event handling proven in
`scripts/spike_03_calendar.py` into a proper pipeline module. See that
file's docstring for the reasoning — lifted verbatim rather than
reinvented.

CLAUDE.md rule 4: read-only scopes only (calendar.readonly). This module
never creates, modifies or deletes calendar entries.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.schemas import CalendarEvent
from app.core.tz import user_tz
from app.ingestion.drops import drop_id, log_drop, report_drops

log = logging.getLogger(__name__)

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
    """Returns (datetime in the user's timezone, is_all_day)."""
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
        a.get("self") and a.get("responseStatus") == "declined" for a in raw_attendees
    )


def event_to_calendar_event(
    raw: dict[str, Any],
    tz: ZoneInfo,
    *,
    calendar_id: str | None = None,
    owned: bool = True,
) -> CalendarEvent | None:
    if raw.get("status") == "cancelled":
        return None

    attendees_raw = raw.get("attendees", [])
    if user_declined(attendees_raw):
        return None

    start_raw = raw.get("start")
    end_raw = raw.get("end")
    if not start_raw or not end_raw:
        raise ValueError(f"event {raw.get('id')} missing start/end")

    start, start_all_day = parse_event_time(start_raw, tz)
    end, end_all_day = parse_event_time(end_raw, tz)
    if end_all_day:
        # Google reports an all-day event's `end.date` as the day AFTER the
        # last day it covers — the range is half-open. Stored verbatim, the
        # one-day offsite in tests/fixtures/calendar_events.json came out as
        # "2026-08-22T00:00 to 2026-08-23T00:00", and
        # script_chain._format_calendar hands both isoformat strings
        # straight to the model, so the hosts said a one-day offsite ran
        # into Sunday.
        #
        # A second before the exclusive end, not the previous midnight: that
        # keeps `end > start` for a single-day event (a zero-length event
        # reads as a data error to everything downstream) and keeps the
        # duration honest — the whole of the 22nd, not none of it.
        end -= timedelta(seconds=1)

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
        all_day=start_all_day,
        calendar_id=calendar_id,
        owned=owned,
    )


# ──────────────────────────────────────────────────────────────
# Fetch paths
# ──────────────────────────────────────────────────────────────


def _load_fixture(
    fixture: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    data = json.loads(fixture.read_text())
    calendars = data["calendarList"]["items"]
    events_by_cal = {
        cal_id: payload["items"] for cal_id, payload in data["eventsByCalendar"].items()
    }
    return calendars, events_by_cal


def _fetch_live(
    time_min: datetime, time_max: datetime
) -> tuple[list[dict], dict[str, list[dict]]]:
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "google-api-python-client is not installed; cannot fetch Calendar "
            "live. Install it or pass fixture= for the offline path."
        ) from exc

    scripts_dir = Path(__file__).resolve().parents[3] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from _google_auth import get_credentials

    # Deliberately NOT re-wrapped in a plain RuntimeError. MissingCredentialsError
    # already is one, so the documented contract holds, and flattening it threw
    # away the ExpiredCredentialsError subtype — the single most likely
    # production failure here, and the only one with a known permanent fix.
    # app.cli keys its loudest error off that type, and it cannot do that if
    # the type is discarded one frame after it is raised.
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


async def fetch_events(
    *, fixture: Path | None = None, hours_ahead: int = 72
) -> list[CalendarEvent]:
    """Fetch and normalise upcoming Calendar events across all calendars.

    fixture=<path> reads that JSON file instead of hitting the network.
    fixture=None hits the live Calendar API and raises a clear, actionable
    RuntimeError naming the exact missing credential if it cannot run.
    """
    # Through the shared resolver, so a bad zone degrades to UTC with the
    # rest of the briefing instead of failing this source outright.
    tz = user_tz()

    if fixture is not None:
        calendars, events_by_cal = _load_fixture(fixture)
    else:
        now = datetime.now(tz)
        window_end = now + timedelta(hours=hours_ahead)
        calendars, events_by_cal = _fetch_live(now, window_end)

    all_events: list[CalendarEvent] = []
    considered = 0
    dropped = 0
    for cal in calendars:
        cal_id = cal["id"]
        # Google reports accessRole per calendar. Subscribed holiday, week
        # number, religious and contact-birthday calendars come back as
        # "reader" or "freeBusyReader"; calendars the user actually keeps a
        # schedule on are "owner" or "writer". That distinction is what lets
        # ambient entries be ranked down instead of walked chronologically.
        owned = cal.get("accessRole") in ("owner", "writer")
        for raw_event in events_by_cal.get(cal_id, []):
            considered += 1
            try:
                event = event_to_calendar_event(
                    raw_event, tz, calendar_id=cal_id, owned=owned
                )
            except Exception:
                # A malformed event must never take down the whole run
                # (CLAUDE.md rule 7 — graceful degradation) — but it must be
                # SEEN. See app/ingestion/drops.py.
                dropped += 1
                log_drop(log, "calendar", drop_id(raw_event))
                continue
            # A None here is a deliberate filter, not a failure: cancelled
            # events and ones the user declined are supposed to disappear.
            # Counting them as drops would fire the escalation on a healthy
            # calendar that simply had a cancelled standup on it.
            if event is not None:
                all_events.append(event)

    report_drops(log, "calendar", total=considered, dropped=dropped)

    all_events.sort(key=lambda e: e.start)
    return all_events
