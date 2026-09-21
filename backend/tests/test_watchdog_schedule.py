"""A scheduled watchdog entry must actually reach `app.cli check`.

`briefing-watchdog.yml` stands a scheduled run down when the local hour is
outside 03:00-12:00:

    if [ "$EVENT_NAME" = "schedule" ] && [ -z "$BRIEFING_ID" ]; then
      local_hour=$(TZ="${USER_TIMEZONE:-UTC}" date +%-H)
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
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WATCHDOG = ROOT / ".github" / "workflows" / "briefing-watchdog.yml"
BRIEFING = ROOT / ".github" / "workflows" / "briefing.yml"

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
    """A floor tighter than the real gap between the newest generation
    slot and the check cries wolf every day, and a watchdog that is
    usually wrong stops being read — which this workflow's own header
    says in as many words.

    A punctual scheduler is the RIGHT model here, and that is worth
    stating because review flagged the opposite for the earlier design.
    `freshness` measures the newest briefing ANYWHERE, so a generation
    run that lands LATE makes the newest briefing fresher at check time,
    never staler. Lateness cannot cause a false alarm; only a slot that
    does not happen at all can, and that is a true alarm.
    """
    floor = re.search(r"--max-age-hours (\d+)", WATCHDOG.read_text())
    assert floor, "the watchdog no longer passes a freshness floor"
    floor_hours = int(floor.group(1))

    gen_hours = sorted(_utc_hour_minute(c)[0] for c in _crons(BRIEFING))
    worst = 0.0
    for cron in _crons(WATCHDOG):
        hour, minute = _utc_hour_minute(cron)
        at = hour + minute / 60
        # The newest generation slot at or before this check, wrapping.
        newest = max((g for g in gen_hours if g <= at), default=None)
        age = at - newest if newest is not None else at + 24 - max(gen_hours)
        worst = max(worst, age)

    assert worst < floor_hours, (
        f"a healthy system reaches {worst:.1f}h at some checkpoint, past "
        f"the {floor_hours}h floor — this fires every day on a working "
        "pipeline"
    )


def test_the_floor_is_below_a_single_dropped_slot():
    """Loose is not the same as useless. One missed generation slot must
    still be visible, or the floor only catches catastrophes."""
    floor = int(re.search(r"--max-age-hours (\d+)", WATCHDOG.read_text()).group(1))
    gen = sorted(_utc_hour_minute(c)[0] for c in _crons(BRIEFING))
    checks = sorted(
        h + m / 60 for h, m in (_utc_hour_minute(c) for c in _crons(WATCHDOG))
    )
    earliest_check = checks[0]
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
    """And loose is not the same as useless. If every run after the first
    of the day fails — the 2026-09-20 shape — the morning check must fire.
    """
    floor = int(re.search(r"--max-age-hours (\d+)", WATCHDOG.read_text()).group(1))
    # Last good briefing 20:05 UTC; earliest morning check 05:52 the next
    # day. That is the incident this was written for.
    stalled_age = (24 - 20.08) + 5.87
    assert stalled_age > floor, (
        f"the 2026-09-20 incident is {stalled_age:.1f}h at the first "
        f"morning check and the floor is {floor}h, so it would pass"
    )
