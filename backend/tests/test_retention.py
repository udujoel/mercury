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
from app.core.schemas import BriefingManifest, Turn
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
    ],
)
def test_briefing_id_from_key(key, expected):
    assert briefing_id_from_key(key) == expected


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


def test_prune_reports_a_bad_window_as_a_configuration_error(
    monkeypatch, tmp_path
):
    storage = _storage_with(tmp_path, TODAY)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    monkeypatch.setattr("app.cli._today_briefing_id", lambda _tz: TODAY)

    assert main(["prune", "--retention-days", "0"]) == 2
    assert storage.load_manifest(TODAY) is not None
