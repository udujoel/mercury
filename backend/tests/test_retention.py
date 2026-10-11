"""
Retention: deleting old briefings without ever deleting a current one.

Nothing deleted anything before this existed, so storage grew forever — ~3MB
of audio plus a manifest every morning against a 10GB free tier. (The docs
claimed a 7-day retention for months. There was no such thing anywhere in the
code or the configuration.)

The risk in the fix is the obvious one: code whose job is deletion, running
unattended every morning, next to the one object the user is about to listen
to. So most of what is asserted here is what must SURVIVE.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.cli import main
from app.core.schemas import (
    BriefingDigest,
    BriefingManifest,
    DigestNewsletter,
    Turn,
)
from app.synthesis.storage import (
    LocalFilesystemStorage,
    briefing_id_from_key,
    prune_briefings,
)

TODAY = "2026-08-30"


def _manifest(briefing_id: str) -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=1000,
        audio_url=f"/briefing/{briefing_id}/audio",
        segments=[{"id": "cold_open", "label": "Good morning", "start_ms": 0}],
        turns=[Turn(speaker="A", segment="cold_open", text="Morning.")],
        sources=[],
    )


def _digest(briefing_id: str) -> BriefingDigest:
    """A digest with a real newsletter in it — the point of the test below is
    that a record of who mailed the user survives nothing."""
    return BriefingDigest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        newsletters=[
            DigestNewsletter(
                id="nl_1",
                source_name="Stratechery",
                sender="ben@stratechery.com",
                subject="The Gemini Moat",
                received_at=datetime.now(UTC),
            )
        ],
    )


def _storage_with(tmp_path, *briefing_ids: str) -> LocalFilesystemStorage:
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    for briefing_id in briefing_ids:
        storage.save(briefing_id, b"audio bytes", "wav", _manifest(briefing_id))
    return storage


# ── the thing that must never happen ─────────────────────────


def test_todays_briefing_is_never_deleted(tmp_path):
    """The whole point. A retention job that deletes the briefing the user is
    about to listen to has done more damage than unbounded growth ever
    would."""
    storage = _storage_with(tmp_path, TODAY, "2026-01-01")

    prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert storage.load_manifest(TODAY) is not None
    assert storage.load_audio(TODAY) is not None


def test_todays_briefing_survives_the_shortest_legal_window(tmp_path):
    """Even at retention_days=1, where the cutoff is as close to today as the
    arithmetic allows it to get."""
    storage = _storage_with(tmp_path, TODAY, "2026-08-29", "2026-08-01")

    prune_briefings(storage, retention_days=1, today_id=TODAY)

    assert storage.load_manifest(TODAY) is not None
    assert storage.load_manifest("2026-08-29") is not None
    assert storage.load_manifest("2026-08-01") is None


def test_a_zero_day_window_is_refused_rather_than_obeyed(tmp_path):
    """`RETENTION_DAYS=0` is a misconfiguration, not an instruction to delete
    everything including this morning."""
    storage = _storage_with(tmp_path, TODAY)

    with pytest.raises(ValueError, match="at least 1"):
        prune_briefings(storage, retention_days=0, today_id=TODAY)

    assert storage.load_manifest(TODAY) is not None


def test_a_future_dated_briefing_is_left_alone(tmp_path):
    """`--briefing-id` accepts any date, so a briefing dated ahead of today can
    exist. It is not old, whatever the arithmetic says."""
    storage = _storage_with(tmp_path, "2026-09-15")

    prune_briefings(storage, retention_days=1, today_id=TODAY)

    assert storage.load_manifest("2026-09-15") is not None


def test_unrecognised_files_are_left_alone(tmp_path):
    """This function deletes things. Anything it cannot positively identify as
    a briefing is not its business."""
    storage = _storage_with(tmp_path, "2026-01-01")
    (tmp_path / "notes.txt").write_text("keep me")
    (tmp_path / "README").write_text("keep me too")

    prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert (tmp_path / "notes.txt").exists()
    assert (tmp_path / "README").exists()


# ── and the thing that must happen ───────────────────────────


def test_briefings_past_the_window_are_deleted_entirely(tmp_path):
    storage = _storage_with(tmp_path, "2026-01-01", TODAY)

    deleted = prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert deleted == ["2026-01-01"]
    assert storage.load_manifest("2026-01-01") is None
    # Audio too, not just the manifest — the audio is 3MB of the 3MB.
    assert storage.load_audio("2026-01-01") is None


def test_the_boundary_day_is_kept(tmp_path):
    """30 days of retention means 30 days are there, not 29."""
    storage = _storage_with(tmp_path, "2026-07-31", "2026-08-01", TODAY)

    deleted = prune_briefings(storage, retention_days=30, today_id=TODAY)

    # 2026-08-30 minus 30 days is 2026-07-31, so that is the cutoff itself.
    assert deleted == []
    assert storage.load_manifest("2026-07-31") is not None


def test_audio_orphaned_by_a_half_failed_run_is_still_cleaned_up(tmp_path):
    """Listing is keyed off object names, not manifests, so audio left behind
    by a run that died between writing the audio and writing the manifest is
    still found."""
    storage = _storage_with(tmp_path, "2026-01-01")
    (tmp_path / "2026-01-01.manifest.json").unlink()

    assert storage.list_briefing_ids() == ["2026-01-01"]
    prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert not (tmp_path / "2026-01-01.wav").exists()


def test_one_undeletable_briefing_does_not_stop_the_rest(tmp_path, monkeypatch):
    """Retention runs in the same workflow as generation. It must never be the
    reason a morning reports failure."""
    storage = _storage_with(tmp_path, "2026-01-01", "2026-01-02")
    original = storage.delete_briefing

    def _sometimes_fails(briefing_id: str) -> None:
        if briefing_id == "2026-01-01":
            raise OSError("permission denied")
        original(briefing_id)

    monkeypatch.setattr(storage, "delete_briefing", _sometimes_fails)

    deleted = prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert deleted == ["2026-01-02"]


# ── key parsing ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("2026-08-30.manifest.json", "2026-08-30"),
        ("2026-08-30.m4a", "2026-08-30"),
        ("2026-08-30.wav", "2026-08-30"),
        ("notes.txt", None),
        ("2026-8-3.wav", None),
        ("backup/2026-08-30.wav", None),
        ("2026-08-30", None),
        # Date-SHAPED is not a date. The regex alone accepted all of these,
        # and the only consumer of this function is the code that deletes.
        ("2026-01-99.wav", None),
        ("2026-02-30.m4a", None),
        ("2026-13-01.manifest.json", None),
        ("2026-00-10.wav", None),
        # A real leap day must still parse — a validator that is too strict
        # orphans one briefing a year and nothing ever cleans it up.
        ("2024-02-29.m4a", "2024-02-29"),
    ],
)
def test_briefing_id_from_key(key, expected):
    assert briefing_id_from_key(key) == expected


def test_a_date_shaped_impossible_id_is_not_treated_as_a_briefing(tmp_path):
    """`2026-01-99` sorts before every cutoff, so retention read it as very
    old and deleted it. Nothing this function cannot positively identify as a
    calendar date is its business."""
    storage = _storage_with(tmp_path, TODAY)
    (tmp_path / "2026-01-99.wav").write_bytes(b"not a briefing")
    (tmp_path / "2026-02-30.manifest.json").write_text("{}")

    deleted = prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert deleted == []
    assert (tmp_path / "2026-01-99.wav").exists()
    assert (tmp_path / "2026-02-30.manifest.json").exists()
    assert storage.list_briefing_ids() == [TODAY]


def test_delete_briefing_removes_every_object_for_that_briefing(tmp_path):
    """Retention used to remove the manifest plus m4a and wav only, so any
    other object for the briefing survived the deletion that was supposed to
    reclaim its space — and nothing ever looked at it again."""
    storage = _storage_with(tmp_path, "2026-01-01")
    (tmp_path / "2026-01-01.mp3").write_bytes(b"a format this layer never wrote")
    (tmp_path / "2026-01-01.script.json").write_text("{}")

    prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_retention_removes_the_digest_too(tmp_path):
    """A digest left behind by retention is an orphaned record of someone's
    inbox — every newsletter that arrived that morning, who sent it and
    when — sitting in the bucket after the briefing it belonged to is gone.
    That is a different and worse failure than the 10GB one the sweep was
    written for, so it gets its own regression test rather than relying on
    the general "every object" case above.

    `delete_briefing` needed no change: it already sweeps every key whose
    prefix is the briefing id. This exists so a future refactor back to an
    explicit key list cannot quietly reintroduce it — stubbing
    `LocalFilesystemStorage.delete_briefing` down to
    `_manifest_path` + KNOWN_AUDIO_FORMATS fails here and nowhere else.
    """
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    storage.save(
        "2026-01-01",
        b"audio bytes",
        "wav",
        _manifest("2026-01-01"),
        _digest("2026-01-01"),
    )
    digest_path = tmp_path / "2026-01-01.digest.json"
    assert digest_path.exists(), "the fixture never wrote a digest"
    assert "Stratechery" in digest_path.read_text()

    prune_briefings(storage, retention_days=30, today_id=TODAY)

    assert not digest_path.exists()
    assert storage.load_digest("2026-01-01") is None
    assert sorted(p.name for p in tmp_path.iterdir()) == []


def test_a_failed_delete_is_not_reported_as_deleted(tmp_path, monkeypatch):
    """prune returns what it deleted, and the caller logs that list. A
    delete_briefing that swallows its own failures makes that list a claim
    about objects still sitting in the bucket."""
    storage = _storage_with(tmp_path, "2026-01-01")

    def _fails(briefing_id: str) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(storage, "delete_briefing", _fails)

    assert prune_briefings(storage, retention_days=30, today_id=TODAY) == []


# ── the CLI wrapper ──────────────────────────────────────────


def test_prune_command_uses_the_configured_window(monkeypatch, tmp_path):
    storage = _storage_with(tmp_path, "2026-01-01", TODAY)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    monkeypatch.setattr("app.cli._today_briefing_id", lambda _tz: TODAY)

    assert main(["prune", "--retention-days", "30"]) == 0
    assert storage.load_manifest("2026-01-01") is None
    assert storage.load_manifest(TODAY) is not None


def test_prune_dry_run_names_what_would_go_without_deleting_it(
    monkeypatch, tmp_path, caplog
):
    """A dry run that only says "nothing deleted" is not worth running. It has
    to name the same briefings the real run would, or it is not a preview of
    anything."""
    storage = _storage_with(tmp_path, "2026-01-01", "2026-08-29", TODAY)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    monkeypatch.setattr("app.cli._today_briefing_id", lambda _tz: TODAY)

    with caplog.at_level("INFO", logger="mercury.cli"):
        assert main(["prune", "--dry-run", "--retention-days", "30"]) == 0

    message = caplog.text
    assert "2026-01-01" in message
    assert "2026-08-29" not in message
    assert TODAY not in message

    assert storage.load_manifest("2026-01-01") is not None


def test_prune_reports_a_bad_window_as_a_configuration_error(monkeypatch, tmp_path):
    storage = _storage_with(tmp_path, TODAY)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    monkeypatch.setattr("app.cli._today_briefing_id", lambda _tz: TODAY)

    assert main(["prune", "--retention-days", "0"]) == 2
    assert storage.load_manifest(TODAY) is not None


@pytest.mark.parametrize("window", ["0", "-1"])
def test_dry_run_refuses_a_window_the_real_command_would_refuse(
    monkeypatch, tmp_path, window
):
    """A preview of a command that cannot run is not a preview. `--dry-run
    --retention-days 0` used to list today's briefing as doomed and exit 0,
    which reads as "this is what would happen" about something that would in
    fact be rejected."""
    storage = _storage_with(tmp_path, "2026-01-01", TODAY)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    monkeypatch.setattr("app.cli._today_briefing_id", lambda _tz: TODAY)

    assert main(["prune", "--dry-run", "--retention-days", window]) == 2


# ── several briefings a day ────────────────────────────────────


def test_an_id_may_carry_a_time_of_day():
    """`YYYY-MM-DD-HHMM`, so four runs a day stop overwriting each other.

    The suffix is range-checked rather than trusted, for the same reason the
    date is: `2026-01-02-2599` matches the shape and is not a time, and an id
    nothing can parse is a key retention can never age out.
    """
    from app.synthesis.storage import briefing_day, is_briefing_id

    assert is_briefing_id("2026-09-07-0412")
    assert is_briefing_id("2026-09-07-2359")
    assert not is_briefing_id("2026-09-07-2400"), "24:00 is not an hour"
    assert not is_briefing_id("2026-09-07-0360"), "60 is not a minute"
    assert not is_briefing_id("2026-09-07-041"), "not four digits"

    # Every briefing generated before this change is day-only, and there are
    # fourteen of them live in R2. They stay valid ids — the alternative to
    # backwards compatibility here is an outage, not a migration.
    assert is_briefing_id("2026-09-07")
    assert briefing_day("2026-09-07-0412") == "2026-09-07"
    assert briefing_day("2026-09-07") == "2026-09-07"


def test_a_timed_id_is_recovered_from_its_storage_key():
    from app.synthesis.storage import briefing_id_from_key

    assert briefing_id_from_key("2026-09-07-0412.manifest.json") == "2026-09-07-0412"
    assert briefing_id_from_key("2026-09-07.manifest.json") == "2026-09-07"
    assert briefing_id_from_key("settings.json") is None


def test_retention_keeps_or_drops_a_whole_day(tmp_path):
    """The window is a number of DAYS, so a morning goes as a unit.

    Two things would have broken here. `date.fromisoformat(today_id)` raises
    on a timed id, which would have made housekeeping the step that failed
    the run; and comparing whole ids rather than day prefixes would let four
    briefings from one day straddle a cutoff on the strength of their minute.
    """
    from app.synthesis.storage import LocalFilesystemStorage, prune_briefings

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    ids = [
        "2026-09-07-0412",
        "2026-09-07-1012",  # today, two runs
        "2026-09-06-0412",
        "2026-09-06-2212",  # yesterday, two runs
        "2026-09-01",  # old, and day-only
        "2026-09-01-1612",  # old, and timed
    ]
    for briefing_id in ids:
        (tmp_path / f"{briefing_id}.manifest.json").write_text("{}")

    deleted = prune_briefings(storage, retention_days=3, today_id="2026-09-07-1012")

    assert sorted(deleted) == [
        "2026-09-01",
        "2026-09-01-1612",
    ], "the old day goes whole, in both id shapes"
    kept = set(storage.list_briefing_ids())
    assert "2026-09-07-0412" in kept, "an earlier run of TODAY is not old"
    assert "2026-09-06-2212" in kept


def test_a_new_id_never_sorts_before_one_that_exists(tmp_path, monkeypatch):
    """Two ways to produce an id that is newer and looks older.

    **Same minute.** Two runs starting inside one minute produced the same
    id, so the second was skipped by generation's idempotency check — or
    overwrote the first with `--force`. The extra briefing did not exist.

    **Crossing local midnight.** `--briefing-day` pins the DAY, so a
    checkpoint deciding at 23:58 with a job starting at 00:01 produces
    `<yesterday>-0001` — which sorts BEFORE every other run of that day.
    Newest-first is lexicographic everywhere here, so the freshest briefing
    would be filed as the oldest and the phone would show a stale one.
    """
    from app import cli
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    monkeypatch.setattr(cli, "build_storage", lambda: storage, raising=False)
    monkeypatch.setattr("app.synthesis.storage.build_storage", lambda: storage)

    # An empty day takes the clock, unchanged.
    assert cli._free_briefing_id("2026-09-07", "0412") == "2026-09-07-0412"

    for existing in ["2026-09-07-0412", "2026-09-07-2212"]:
        (tmp_path / f"{existing}.manifest.json").write_text("{}")

    # Same minute as the newest: must not collide with it.
    assert cli._free_briefing_id("2026-09-07", "2212") == "2026-09-07-2213"

    # A run that crossed midnight: the clock says 00:01, and that would sort
    # first. It goes after the newest instead.
    late = cli._free_briefing_id("2026-09-07", "0001")
    assert late == "2026-09-07-2213"
    assert late > "2026-09-07-2212", "the newest run must sort newest"


def test_the_clock_wins_when_it_is_genuinely_later(tmp_path, monkeypatch):
    """The bump is a floor, not a replacement — otherwise every run of a day
    would be one minute after the last rather than when it happened."""
    from app import cli
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    monkeypatch.setattr("app.synthesis.storage.build_storage", lambda: storage)
    (tmp_path / "2026-09-07-0412.manifest.json").write_text("{}")

    assert cli._free_briefing_id("2026-09-07", "1012") == "2026-09-07-1012"


def test_an_id_that_would_not_survive_its_own_key_is_refused():
    """`<id>.<artifact>` is the storage key, so a dot in an id truncates it.

    `is_briefing_id` asked a key-shaped regex about `f"{value}."` and `match`
    only anchors the start — so `2026-09-07-0412.5` matched and the trailing
    `.5` was ignored. It was a valid id that could be written and never read
    back, which is the worst kind of valid.
    """
    from app.synthesis.storage import is_briefing_id

    assert not is_briefing_id("2026-09-07-0412.5")
    assert not is_briefing_id("2026-09-07.manifest")
    assert not is_briefing_id("2026-09-07-0412 ")
    assert is_briefing_id("2026-09-07-0412")


def test_a_full_late_hour_finds_the_next_free_minute(tmp_path, monkeypatch):
    """The clamp collided at exactly the minute it was protecting.

    Clamping the bump at 23:59 meant that with a briefing already at 23:59
    and an earlier clock, the "next" id WAS 23:59 — the collision the
    function exists to prevent, produced by the code preventing it.
    """
    from app import cli
    from app.synthesis.storage import LocalFilesystemStorage

    storage = LocalFilesystemStorage(base_dir=tmp_path)
    monkeypatch.setattr("app.synthesis.storage.build_storage", lambda: storage)

    for taken in ["2026-09-07-2357", "2026-09-07-2359"]:
        (tmp_path / f"{taken}.manifest.json").write_text("{}")

    # 23:59 is the newest, so nothing can sort after it. The run must still
    # get a name — a NAMING concern costing the morning is the failure rule
    # 7 exists to prevent — and it must not be one already taken.
    got = cli._free_briefing_id("2026-09-07", "0412")
    assert got not in {
        "2026-09-07-2359",
        "2026-09-07-2357",
    }, f"collided with an existing briefing: {got}"
    assert got.startswith("2026-09-07-"), got
