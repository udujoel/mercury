# Spike 3 — Calendar fetch, today + 72h, across all calendars

Status: offline path fully proven with fixtures. Live path implemented and
ready but unproven — no `credentials.json` exists on this machine (see
`BRIEF.md`, "Critical environment fact").

Script: `scripts/spike_03_calendar.py`
Shared auth: `scripts/_google_auth.py`
Fixture: `backend/tests/fixtures/calendar_events.json`
Tests: `backend/tests/test_spike_03_calendar.py` (16 tests)

## What was proven offline

- **All calendars, not just primary.** The fixture models
  `calendarList.list` returning three calendars (`primary`, `Work`,
  `Family`); the script enumerates all three and fetches events on each,
  matching the live code path (`fetch_live` calls `calendarList().list()`
  then loops `events().list()` per calendar, with pagination via
  `nextPageToken`).
- **All-day vs timed events.** `parse_event_time` branches on whether the
  API gives a `date` key (all-day — no time component, parsed as local
  midnight in the target zone) or a `dateTime` key (timed, parsed and
  converted with `astimezone`). Verified against a fixture all-day event
  (`Company Offsite`) and confirmed `start.hour == 0`.
- **Recurring event expansion.** Because `events.list` is called with
  `singleEvents=True`, recurrence is already expanded server-side into
  concrete instances carrying a `recurringEventId` back-reference — the
  fixture models one such instance (`Daily Standup`) and the normaliser
  treats it like any other event. No client-side RRULE expansion needed or
  attempted.
- **Declined and cancelled exclusion.** `user_declined` checks the user's
  own attendee entry (`self: True`) for `responseStatus == "declined"`;
  `event_to_calendar_event` also short-circuits on `status == "cancelled"`.
  Both a declined event (`Optional Design Review`) and a cancelled one
  (`Cancelled Standup`) are present in the fixture and confirmed absent
  from the final output — 7 raw fixture events in, 5 survive.
- **Timezone conversion — the risky part.** The fixture includes a meeting
  authored in `America/New_York` at `09:00-04:00` (EDT). Converted into
  `USER_TIMEZONE` (`Europe/Lisbon`, `+01:00` WEST in August), it lands at
  `14:00` local — verified exactly in both the test suite and the printed
  day-grouped view. `zoneinfo.ZoneInfo` is used throughout, no manual UTC
  offset arithmetic.
- **Conference boilerplate stripped, join URL kept.** `extract_join_url`
  reads `conferenceData.entryPoints` first (the structured field Google
  actually provides), falling back to a regex over the description for
  bare Meet/Zoom/Teams links. `strip_conference_boilerplate` removes lines
  like "Or dial: +1 555-0123" while the join URL is explicitly re-appended
  to the cleaned description as `Join: <url>` so it survives even though
  the boilerplate line it originally lived in was cut.
- **`needs_prep` heuristic.** True if the description has a Google Docs
  link, if there are 3+ attendees (including the user), or if the
  description contains prep language ("agenda", "please review", ...).
  Verified: the 5-attendee budget review with an agenda doc → `True`; the
  2-person daily standup with no description → `False`.
- **Attendee extraction.** `extract_attendees` returns display names
  (falling back to email) and explicitly excludes the user's own `self:
  True` entry, since showing the user in their own attendee list adds
  nothing.

## Real output (`--fixture` mode)

```
$ backend/.venv/bin/python scripts/spike_03_calendar.py --fixture
[fixture] reading /Users/joud/sandbox/ML/mercury/backend/tests/fixtures/calendar_events.json
3 calendars found: ['udujoel@gmail.com', 'Work', 'Family']

== 2026-08-21 (Friday) ==
  09:30  Daily Standup — 1 attendee(s)
  14:00  Sync with NYC Team — 1 attendee(s) [needs prep]
  16:00  Q3 Budget Review — 4 attendee(s) [needs prep]

== 2026-08-22 (Saturday) ==
  all day  Company Offsite (All Day)

== 2026-08-23 (Sunday) ==
  10:00  Kids Dentist Appointment

5 events across 3 calendars, 0 skipped.
```

Declined (`Optional Design Review`) and cancelled (`Cancelled Standup`)
events are correctly absent from the printed view. `--json` output for the
cross-timezone event confirms:

```json
{
  "id": "evt_crosstz_001",
  "title": "Sync with NYC Team",
  "start": "2026-08-21T14:00:00+01:00",
  "description": "Weekly sync.\n\nAgenda: review NYC launch metrics.\n\nJoin: https://meet.google.com/abc-defg-hij",
  "attendees": ["Jordan Lee"],
  "needs_prep": true
}
```

## Test and lint results (verbatim)

```
$ cd backend && ./.venv/bin/python -m pytest -q tests/test_spike_03_calendar.py
................  [100%]
16 passed in 0.1x s
```

```
$ cd backend && ./.venv/bin/ruff check scripts/spike_03_calendar.py scripts/_google_auth.py tests/test_spike_03_calendar.py
All checks passed!
```

(Full-suite `pytest -q` from `backend/` shows 2 unrelated failures in
`tests/test_spike_04_script.py`, owned by a different Phase 0 spike agent —
not this spike's files.)

## What needs live credentials (unproven)

- Whether a real Google account's `calendarList.list` response includes
  calendars the user doesn't actually want briefed on (holiday calendars,
  calendars shared read-only by others, etc.) — Phase 1 will likely need a
  calendar allow/deny list, which this spike doesn't attempt.
- Real recurrence edge cases: an instance that was individually modified
  (time changed for one occurrence only) or individually cancelled within
  a series — `singleEvents=True` is documented to handle both correctly,
  but has not been exercised against live data.
- `conferenceData` shapes from providers other than Meet/Zoom (e.g. a
  dial-in-only conference, or a third-party scheduling tool that injects
  its own join link format not covered by `JOIN_URL_RE`).
- Pagination (`nextPageToken`) — the loop in `fetch_live` is written but a
  calendar with enough events in a 72h window to paginate has not been
  observed.
- Whether the OAuth scope `calendar.readonly` is sufficient to read
  calendars the user has been *shared* (vs. calendars they own) — the
  Google docs say yes, but this is unverified locally.

## Exact steps to run live

Same Google Cloud project as Spike 2 (Gmail and Calendar share one OAuth
client and one `credentials.json`/`token.json` pair via `_google_auth.py`):

1. https://console.cloud.google.com/apis/library — enable **Gmail API**
   and **Google Calendar API**.
2. https://console.cloud.google.com/apis/credentials/consent — OAuth
   consent screen in **Testing** mode, your account as sole test user.
3. https://console.cloud.google.com/apis/credentials — **Create
   Credentials → OAuth client ID**, application type **Desktop app**.
4. Download the JSON, save as `credentials.json` at the repo root (already
   gitignored), or set `GOOGLE_CREDENTIALS_FILE`.
5. Run `python scripts/spike_03_calendar.py --live`. First run opens a
   browser for consent and caches to `token.json` (gitignored, overridable
   via `GOOGLE_TOKEN_FILE`).

## Gotchas found

- All-day events use a bare `date` field with no timezone — anchoring them
  at midnight in `USER_TIMEZONE` (rather than UTC midnight) matters, since
  UTC midnight can land on the wrong calendar day for the user depending on
  their offset.
- Declined-event exclusion must check the *user's own* attendee entry
  (`self: True`), not just "any attendee declined" — a meeting where a
  different invitee declined should still show up.
- `conferenceData.entryPoints` is the reliable source for a join URL; a
  regex over the free-text description is only a fallback for
  older-style invites that paste the link directly into the body.
  Preferring the structured field first avoids depending on Google's
  boilerplate wording, which changes.
- `singleEvents=True` is required for `orderBy='startTime'` to work at
  all — the Calendar API rejects `orderBy=startTime` on unexpanded
  recurring series. This spike bakes that into every `events.list` call
  rather than leaving it as an option, since forgetting it would silently
  break the chronological ordering the brief calls for.
