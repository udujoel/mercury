"""Present and playable is not the same as current. ROADMAP task 31.

Every existing assertion in `app.cli check` asks whether a briefing
EXISTS and can be played. None asks whether it is still worth playing,
and two real failures live in that gap:

  - the `00:12` cron generates a briefing named for the NEW day, so all
    five of the Worker's morning checkpoints see "this date has a
    playable briefing" and stand down — the 05:40 alarm included. The
    listener wakes to a briefing assembled from an inbox six hours
    stale. Masked today only by GitHub running that cron four hours
    late; the 25 October DST change unmasks it.
  - on 2026-09-20 two runs failed at 20:44 and 20:48 on a Gemini spend
    cap. The last good briefing was 2.5 hours old, `check` would have
    passed on it, and the watchdog had last run at 11:51.

Both are "the pipeline stopped and the check still said yes".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.cli import _check
from app.core.schemas import BriefingManifest

AUDIO = b"\x00" * 200_000


def _manifest(age_hours: float) -> BriefingManifest:
    return BriefingManifest(
        briefing_id="2026-09-20",
        generated_at=datetime.now(UTC) - timedelta(hours=age_hours),
        duration_ms=420_000,
        audio_url="/briefing/2026-09-20/audio",
        turns=[], segments=[], sources=[],
    )


class _Storage:
    def __init__(self, manifest):
        self.manifest = manifest

    def load_manifest(self, briefing_id):
        return self.manifest

    def load_audio(self, briefing_id):
        return (AUDIO, "audio/mp4")


@pytest.fixture
def storage(monkeypatch):
    def _install(manifest):
        import app.synthesis.storage as st
        monkeypatch.setattr(st, "build_storage", lambda: _Storage(manifest))
        return manifest
    return _install


def _playable(age_hours: float) -> BriefingManifest:
    m = _manifest(age_hours)
    # One turn, so the empty-briefing branch does not fire first.
    m.turns = [{"speaker": "A", "segment": "cold_open", "text": "Morning."}]
    return m


def test_a_fresh_briefing_passes(storage):
    storage(_playable(1.0))
    assert _check("2026-09-20", max_age_hours=7) == 0


def test_a_stale_briefing_gets_its_own_exit_code(storage, capsys):
    """5, not 1, and the distinction is load-bearing.

    Exit 1 means "there is nothing to play", and the watchdog answers it
    by retrying three times and then telling the operator to regenerate.
    Neither response fits a briefing that exists, plays and is merely
    old: retrying cannot make it newer, and regenerating spends real
    Gemini and TTS budget on a briefing that is already there.
    """
    storage(_playable(9.8))
    assert _check("2026-09-20", max_age_hours=9) == 5
    out = capsys.readouterr().out
    assert "hours old" in out
    assert "Do NOT regenerate blindly" in out


def test_without_the_flag_nothing_changes(storage):
    """Opt-in. Every existing caller keeps its behaviour exactly."""
    storage(_playable(99.0))
    assert _check("2026-09-20") == 0


def test_freshness_is_judged_last(storage, capsys):
    """A stale briefing that is ALSO empty must be reported as empty —
    that has its own remedy (`force=true`) and its own exit path."""
    empty = _manifest(99.0)          # no turns
    storage(empty)
    assert _check("2026-09-20", max_age_hours=1) == 1
    assert "empty briefing" in capsys.readouterr().out


def test_a_naive_generated_at_does_not_crash_the_check(storage):
    """Fourth outing for this family in this repo. A naive timestamp
    subtracted from an aware `now` raises TypeError, and the watchdog is
    the last thing that should fall over on a date bug."""
    m = _playable(1.0)
    m.generated_at = m.generated_at.replace(tzinfo=None)
    storage(m)
    assert _check("2026-09-20", max_age_hours=7) == 0


def test_the_flag_reaches_check_from_the_command_line(monkeypatch):
    """The wiring, not the logic — and this session has been a lesson in
    the difference. An argparse flag nobody forwards is the same shape as
    a `topic_centroid` nobody passes."""
    import app.cli as cli

    seen: dict = {}
    monkeypatch.setattr(
        cli, "_check",
        lambda briefing_id, max_age=None, **kw: seen.setdefault(
            "args", (briefing_id, max_age)
        ) or 0,
    )
    monkeypatch.setattr(cli, "_newest_briefing_id", lambda _today: "2026-09-20")
    cli.main(["check", "--briefing-id", "2026-09-20", "--max-age-hours", "7"])

    assert seen["args"][1] == 7.0, (
        "--max-age-hours never reached _check; the flag parses and does "
        "nothing, which is this project's signature bug"
    )


def test_a_briefing_from_the_future_is_reported_not_silently_accepted(
    storage, caplog
):
    """Clock skew between the runner and whatever wrote the manifest gives
    a NEGATIVE age, which passes every floor silently and forever — the
    freshness check quietly stops meaning anything.

    Reported rather than failed: the briefing itself plays, and an alarm
    nobody can act on is the kind this workflow exists to avoid.
    """
    import logging

    m = _playable(1.0)
    m.generated_at = m.generated_at + timedelta(hours=6)
    storage(m)

    with caplog.at_level(logging.WARNING):
        assert _check("2026-09-20", max_age_hours=9) == 0
    assert "FUTURE" in caplog.text


def test_the_boundary_falls_on_the_forgiving_side(storage):
    """Just inside passes, just outside fails.

    Deliberately NOT tested at exactly 9.0: the age is computed against a
    live clock, so microseconds elapse between building the timestamp and
    measuring it and a briefing "exactly at" the floor is really a hair
    past it. Asserting on that measures the test harness, not the code —
    and a test that fails for a reason the code cannot control is one
    somebody eventually deletes.
    """
    storage(_playable(8.9))
    assert _check("2026-09-20", max_age_hours=9) == 0

    storage(_playable(9.1))
    assert _check("2026-09-20", max_age_hours=9) == 5
