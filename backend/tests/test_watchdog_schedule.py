"""A scheduled watchdog entry must actually reach `app.cli check`.

`briefing-watchdog.yml` stands a scheduled run down when the local hour is
outside 03:00-12:00:

    if [ "$EVENT_NAME" = "schedule" ] && [ -z "$BRIEFING_ID" ]; then
      local_hour=<the hour now, in app.core.tz.user_tz()>
      if [ "$local_hour" -lt 3 ] || [ "$local_hour" -ge 12 ]; then
        exit 0
      fi
    fi

The guard is right for what it guards — a morning backstop delayed past
local midnight would ask about the wrong day. But it applies to EVERY
scheduled entry, and a scheduled run never carries a `briefing_id` (only
the Worker's dispatch does). So an entry added at any other hour exits 0
without running the check, and the job goes GREEN.

That is not hypothetical. Task 31's first cut added `42 13`, `42 19` and
`42 1` UTC to catch the other three generation slots. A sweep of all 598
IANA zones found NO timezone in which all three execute; in Europe/Lisbon
all three stand down, summer and winter. The PR was titled "a watchdog for
every slot" and shipped a watchdog for the same one slot as before —
"built and never called", in the change whose whole purpose was detecting
exactly that.

So: this asserts the coupling. Add a cron to that workflow and this fails
until the guard is widened to match.
"""

from __future__ import annotations

import re
import tomllib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = ROOT / ".github" / "workflows" / "briefing-watchdog.yml"
BRIEFING = ROOT / ".github" / "workflows" / "briefing.yml"
WRANGLER = ROOT / "worker" / "wrangler.toml"

# How late GitHub has CREATED a scheduled run on this repo: 12 hours, for
# the old 04:00 generation cron (ADR-010). The watchdog's own backstops ran
# 3.9-7.1 hours late every day from 14 to 25 October 2026, and 9.2 on the
# 12th.
MAX_SCHEDULE_DELAY_H = 12

# The zones a scheduled run can actually resolve to. `USER_TIMEZONE` is
# documented as not optional, with UTC as the fallback the code logs about
# (docs/DEPLOYMENT.md), and Europe/Lisbon is the deployed value.
ZONES = ["UTC", "Europe/Lisbon"]
# One summer date and one winter date, because Lisbon is UTC+1 in summer.
DATES = [(2026, 7, 15), (2026, 12, 15)]


def _crons(path: Path) -> list[str]:
    spec = yaml.safe_load(path.read_text())
    # PyYAML parses the bare key `on:` as the boolean True.
    trigger = spec.get("on") or spec.get(True)
    return [e["cron"] for e in trigger["schedule"]]


def _utc_hour_minute(cron: str) -> tuple[int, int]:
    minute, hour = cron.split()[0], cron.split()[1]
    return int(hour), int(minute)


def _guard_window() -> tuple[int, int]:
    """The stand-down window, read from the workflow rather than repeated.

    A constant copied into the test is a constant that stops agreeing with
    the workflow the first time somebody tunes it.
    """
    source = WATCHDOG.read_text()
    match = re.search(
        r'"\$local_hour"\s+-lt\s+(\d+)\s*\]\s*\|\|\s*\[\s*"\$local_hour"\s+-ge\s+(\d+)',
        source,
    )
    assert match, "the stand-down guard is no longer parseable"
    return int(match.group(1)), int(match.group(2))


def _hours(cron: str) -> float:
    hour, minute = _utc_hour_minute(cron)
    return hour + minute / 60


def _floor() -> int:
    match = re.search(r"--max-age-hours (\d+)", WATCHDOG.read_text())
    assert match, "the watchdog no longer passes a freshness floor"
    return int(match.group(1))


def _worker_morning() -> float:
    """The Worker's first checkpoint, in hours after UTC midnight.

    Every Worker cron before the alarm dispatches generation only if the
    morning is still missing, so on a healthy day the EARLIEST one is the
    run that makes the morning briefing. It is a Cloudflare Cron Trigger and
    punctual, unlike every GitHub entry these tests read.
    """
    crons = tomllib.loads(WRANGLER.read_text())["triggers"]["crons"]
    return min(_hours(c) for c in crons)


def _generation_hours() -> list[float]:
    """Every generation a healthy day has, at its nominal time: the Worker's
    morning run plus each `briefing.yml` schedule entry.

    The Worker has to be in this list. Until 2026-10-25 a GitHub entry at
    00:12 stood in for the morning; with it gone, a list read from
    `briefing.yml` alone has no morning run at all.
    """
    return sorted([_worker_morning(), *(_hours(c) for c in _crons(BRIEFING))])


@pytest.mark.parametrize("cron", _crons(WATCHDOG))
@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("date", DATES)
def test_every_scheduled_watchdog_entry_reaches_the_check(cron, zone, date):
    lo, hi = _guard_window()
    hour, minute = _utc_hour_minute(cron)
    at_utc = datetime(*date, hour, minute, tzinfo=ZoneInfo("UTC"))
    local_hour = at_utc.astimezone(ZoneInfo(zone)).hour

    assert lo <= local_hour < hi, (
        f"cron '{cron}' fires at {local_hour}:00 in {zone} on "
        f"{date[0]}-{date[1]:02d}-{date[2]:02d}, outside the guard's "
        f"{lo}:00-{hi}:00 window — the job exits 0 GREEN without ever "
        "running `app.cli check`. Widen the guard in the same change, or "
        "the entry is decorative."
    )


def test_the_freshness_floor_cannot_fire_on_a_healthy_morning():
    """A floor tighter than the real gap between the newest generation and
    the check cries wolf every day, and a watchdog that is usually wrong
    stops being read — which this workflow's own header says in as many
    words.

    This is the on-time case. `freshness` measures the newest briefing
    ANYWHERE, so a generation run that lands late makes the newest briefing
    fresher at check time, never staler. A CHECK that lands late is the
    opposite, and the next test is about that.
    """
    floor = _floor()
    gen = _generation_hours()
    worst = 0.0
    for cron in _crons(WATCHDOG):
        at = _hours(cron)
        # The newest generation at or before this check, wrapping.
        newest = max((g for g in gen if g <= at), default=None)
        age = at - newest if newest is not None else at + 24 - max(gen)
        worst = max(worst, age)

    assert worst < floor, (
        f"a healthy system reaches {worst:.1f}h at some checkpoint, past "
        f"the {floor}h floor — this fires every day on a working pipeline"
    )


def test_a_late_backstop_does_not_cry_wolf():
    """The backstops run on GitHub's scheduler, and it is late every day.

    From 14 to 25 October 2026 the 05:52 entry ran at 09:44-11:18 and the
    06:56 entry at 11:12-14:03. By then the only briefing a healthy day can
    be counted on to have is the Worker's: GitHub's own generation entries
    are on the same late scheduler and may not have run yet. The 8-hour
    floor was sized for a 00:12 run that no longer exists, and against the
    03:40 run alone it would have fired at 12:31 on 22 October, a morning
    that generated fine.
    """
    floor = _floor()
    morning = _worker_morning()
    for cron in _crons(WATCHDOG):
        late = _hours(cron) + MAX_SCHEDULE_DELAY_H
        age = late - morning
        assert age < floor, (
            f"backstop '{cron}' run {MAX_SCHEDULE_DELAY_H}h late sees a "
            f"morning briefing {age:.1f}h old, past the {floor}h floor — a "
            "false 'Generation has stopped' on a day that generated fine"
        )


def test_the_floor_is_below_a_single_dropped_slot():
    """Loose is not the same as useless. One missed generation slot must
    still be visible, or the floor only catches catastrophes."""
    floor = _floor()
    gen = _generation_hours()
    earliest_check = min(_hours(c) for c in _crons(WATCHDOG))
    # The newest slot at or before the check, and the one before THAT —
    # which is what the check sees when the nearest slot is dropped.
    before = [g for g in gen if g <= earliest_check]
    prior = before[-2] if len(before) >= 2 else max(gen) - 24
    dropped_age = earliest_check - prior
    assert dropped_age > floor, (
        f"a single dropped slot leaves the newest briefing "
        f"{dropped_age:.1f}h old at the {earliest_check:.2f} check, inside "
        f"the {floor}h floor — one missed run would be invisible"
    )


def test_the_floor_still_catches_a_stalled_pipeline():
    """The 2026-09-20 shape: a quota runs out during the day and every run
    after it fails. On 2026-10-25 it happened again. The morning run
    succeeded, Gemini's monthly spend cap was hit before the next one, and
    every later run failed with 429. On two runs a day the last good
    briefing is the morning's, so by the first check of the next morning it
    is a day old.

    The incident as it happened on four runs a day (last good 20:05, check
    at 05:52, 9.8h) is no longer one this floor catches by itself. It does
    not have to: there is no briefing for the new day, so `check` exits 1
    at that same checkpoint. `cli._freshness`'s docstring records that it
    did on the morning after 2026-09-20.
    """
    floor = _floor()
    earliest_check = min(_hours(c) for c in _crons(WATCHDOG))
    stalled_age = 24 - _worker_morning() + earliest_check
    assert stalled_age > floor, (
        f"a pipeline that stopped after yesterday's morning run is "
        f"{stalled_age:.1f}h stale at the first morning check and the "
        f"floor is {floor}h, so it would pass"
    )
