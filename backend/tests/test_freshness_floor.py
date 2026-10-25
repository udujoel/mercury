"""Has generation stopped? ROADMAP task 31.

`check` is DAY-SCOPED: it resolves today's id and asks whether that
briefing exists and plays. That is the wrong shape for "has the pipeline
stopped", and review of #59 priced the confusion exactly. A
`--max-age-hours` flag bolted onto `check` was inert two ways over:

  - a briefing filed under today can never be older than the time since
    local midnight, which at the 05:52 checkpoint is at most 6.9 hours —
    so any floor loose enough to survive a healthy morning could never
    fire at all;
  - and on the morning after the 2026-09-20 stall there was no briefing
    for that day, so `check` returned 1 and the floor was never reached.

`freshness` asks about the newest briefing ANYWHERE. No date to resolve,
so no wrong-day alarm and no dependence on the hour it runs.

**Every test here enters through `cli.main` with only STORAGE stubbed.**
The previous cut monkeypatched `_check` itself with a lambda whose
parameter was named `max_age`, so it observed nothing about the real
signature: a mutation inserting one parameter before `max_age_hours`
silently disabled freshness in production and all eighteen tests stayed
green. A test that stubs the function under test is testing its own stub.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import cli
from app.core.schemas import BriefingManifest

AUDIO = b"\x00" * 200_000


def _manifest(briefing_id: str, age_hours: float) -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC) - timedelta(hours=age_hours),
        duration_ms=420_000,
        audio_url=f"/briefing/{briefing_id}/audio",
        turns=[{"speaker": "A", "segment": "cold_open", "text": "Morning."}],
        segments=[],
        sources=[],
    )


class _Storage:
    """Only the three methods `freshness` uses. A fake that accepts
    anything cannot fail when the real protocol changes."""

    def __init__(self, manifests, *, list_raises=False, load_raises=False):
        self._manifests = manifests
        self._list_raises = list_raises
        self._load_raises = load_raises

    def list_briefing_ids(self):
        if self._list_raises:
            raise RuntimeError("R2 said no")
        return sorted(self._manifests)

    def load_manifest(self, briefing_id):
        if self._load_raises:
            raise RuntimeError("R2 said no")
        return self._manifests.get(briefing_id)


@pytest.fixture
def storage(monkeypatch):
    def _install(manifests, **kw):
        import app.synthesis.storage as st

        built = _Storage(manifests, **kw)
        monkeypatch.setattr(st, "build_storage", lambda: built)
        return built

    return _install


def _run(max_age_hours: float) -> int:
    """Through the real argparse and the real dispatch."""
    return cli.main(["freshness", "--max-age-hours", str(max_age_hours)])


def _production_floor() -> int:
    """The floor the watchdog actually passes, read from the workflow.

    The scenario tests below used a hardcoded 8 that matched production
    until 2026-10-25. When the floor moved to 16 they would have gone on
    certifying "the 2026-09-20 stall is caught" while production no longer
    caught it, so they read the real number now.
    """
    workflow = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / "briefing-watchdog.yml"
    ).read_text()
    match = re.search(r"--max-age-hours (\d+)", workflow)
    assert match, "the watchdog no longer passes a freshness floor"
    return int(match.group(1))


# ──────────────────────────────────────────────────────────────
# The incidents this exists for, at the floor production uses. The
# schedule arithmetic behind each age is in test_watchdog_schedule.py.
# ──────────────────────────────────────────────────────────────


def test_a_stall_after_the_morning_run_is_caught(storage, capsys):
    """2026-10-25: the morning run succeeded, Gemini's spend cap was hit
    before the next one, and every later run failed with 429. On two runs
    a day the newest briefing at the next morning's 05:52 check is the
    previous 03:40 one, 26.2 hours back.

    The 2026-09-20 version (last good 20:05, 9.8h at 05:52) was the
    original test here. At the 16h floor freshness no longer catches it
    by itself; `check` does, because there is no briefing for the new day.
    """
    storage({"2026-10-24-0340": _manifest("2026-10-24-0340", 26.2)})
    assert _run(_production_floor()) == 5
    out = capsys.readouterr().out
    assert "newest briefing anywhere" in out
    assert "gh run list" in out


def test_a_healthy_morning_does_not_fire(storage):
    """The Worker's 03:40 briefing seen from the 06:56 backstop arriving
    twelve hours late, at 18:56: 15.3h. On a healthy day the 10:12 run
    may well have landed by then, but this is the case where it has not."""
    storage({"2026-10-26-0340": _manifest("2026-10-26-0340", 15.3)})
    assert _run(_production_floor()) == 0


def test_a_dead_worker_is_caught_once_check_stands_down(storage):
    """No morning briefing, and the backstop lands at 11:00 UTC, noon in
    Lisbon in summer, so `check` stands down. The newest briefing is
    yesterday's 10:12 run, which landed 6.8 hours late at 17:00: 18.0h."""
    storage({"2026-07-14-1700": _manifest("2026-07-14-1700", 18.0)})
    assert _run(_production_floor()) == 5


def test_the_newest_is_what_counts_not_the_first(storage):
    """Ids are date-prefixed and fixed-width, so `max()` is the newest —
    the same property the archive and retention already rely on."""
    storage(
        {
            "2026-09-19-0612": _manifest("2026-09-19-0612", 48.0),
            "2026-09-21-0012": _manifest("2026-09-21-0012", 2.0),
            "2026-09-20-1812": _manifest("2026-09-20-1812", 20.0),
        }
    )
    assert _run(8) == 0


# ──────────────────────────────────────────────────────────────
# It must not cry wolf, and it must not die trying.
# ──────────────────────────────────────────────────────────────


def test_an_empty_bucket_is_not_staleness(storage):
    """A first run, or a fresh bucket. `check` is the command with
    something to say about a missing briefing."""
    storage({})
    assert _run(8) == 0


def test_a_briefing_with_no_manifest_is_left_to_check(storage):
    """Audio written, the run died before the manifest. That shape has
    its own report and its own remedy."""
    storage({"2026-09-21-0012": None})
    assert _run(8) == 0


def test_a_listing_failure_is_not_reported_as_a_stall(storage):
    """Exit 4. A 403 says nothing about whether generation is healthy,
    and reporting it as a stall sends the operator to the wrong place."""
    storage({}, list_raises=True)
    assert _run(8) == 4


def test_an_unreadable_newest_manifest_is_not_a_stall(storage):
    storage({"2026-09-21-0012": _manifest("2026-09-21-0012", 1.0)}, load_raises=True)
    assert _run(8) == 4


def test_misconfigured_storage_exits_2(monkeypatch):
    import app.synthesis.storage as st

    def _boom():
        raise RuntimeError("R2 storage is not configured")

    monkeypatch.setattr(st, "build_storage", _boom)
    assert _run(8) == 2


def test_a_naive_timestamp_does_not_crash_the_alarm(storage):
    """Fourth outing for this family here, and an alarm that dies on a
    date bug is no alarm."""
    m = _manifest("2026-09-21-0012", 1.0)
    m.generated_at = m.generated_at.replace(tzinfo=None)
    storage({"2026-09-21-0012": m})
    assert _run(8) == 0


def test_a_briefing_from_the_future_is_reported_not_silently_accepted(storage, caplog):
    """Clock skew gives a negative age, which passes every floor silently
    and forever — the alarm quietly stops meaning anything."""
    m = _manifest("2026-09-21-0012", -6.0)
    storage({"2026-09-21-0012": m})
    with caplog.at_level(logging.WARNING):
        assert _run(8) == 0
    assert "FUTURE" in caplog.text


def test_the_boundary_falls_on_the_forgiving_side(storage):
    """Deliberately not tested at exactly the floor: the age is measured
    against a live clock, so a briefing "exactly at" it is really a hair
    past. Asserting on that measures the harness, not the code."""
    storage({"a": _manifest("a", 7.9)})
    assert _run(8) == 0
    storage({"a": _manifest("a", 8.1)})
    assert _run(8) == 5


# ──────────────────────────────────────────────────────────────
# The wiring, entered where the omission would live.
# ──────────────────────────────────────────────────────────────


def test_the_flag_reaches_the_command(storage):
    """No stub of `_freshness` — the previous version stubbed the very
    function under test and so could not see a parameter inserted before
    `max_age_hours`. Here the only thing faked is storage, so the flag
    has to survive argparse, dispatch AND the call to mean anything."""
    storage({"a": _manifest("a", 9.0)})
    assert _run(8) == 5, "a 9h-old briefing must trip an 8h floor"
    assert _run(10) == 0, "and must not trip a 10h one"


def test_freshness_needs_no_day_and_no_storage_listing_of_today(storage):
    """It runs BEFORE any date resolution. Routing it through the
    `--briefing-id` machinery is what made the first cut unreachable."""
    storage({"2026-01-01-0612": _manifest("2026-01-01-0612", 2.0)})
    assert _run(8) == 0


def test_the_floor_is_required(capsys):
    """A default would be a floor nobody chose, applied everywhere."""
    with pytest.raises(SystemExit):
        cli.main(["freshness"])
    assert "max-age-hours" in capsys.readouterr().err
