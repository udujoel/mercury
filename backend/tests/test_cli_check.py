"""
Tests for `python -m app.cli check` — the ADR-010 watchdog probe.

The point of this command is to notice the failures that leave no failed run
behind. GitHub documents its scheduler as best-effort ("some queued jobs may
be dropped"), and a dropped run produces no run record, so nothing fails and
no email is sent. The only way to find out is to ask storage.

So the cases that matter here are the ones that LOOK like success:
a briefing that was never generated, a briefing with no audible turns in it,
and a manifest whose audio is not actually there.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.cli import main
from app.core.schemas import BriefingManifest, Turn
from app.synthesis.storage import LocalFilesystemStorage


def _manifest(
    briefing_id: str = "2026-08-21",
    *,
    turns: list[Turn] | None = None,
    duration_ms: int = 1000,
) -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=duration_ms,
        audio_url=f"/briefing/{briefing_id}/audio",
        segments=[{"id": "cold_open", "label": "Good morning", "start_ms": 0}],
        turns=[Turn(speaker="A", segment="cold_open", text="Morning.")]
        if turns is None
        else turns,
        sources=[],
    )


# Enough bytes to be audio. `check` now has a size floor, because a zero-byte
# object passed the old `audio is None` test and shipped a green run over a
# briefing the phone could not play.
PLAUSIBLE_AUDIO = b"RIFF" + b"\0" * 4096


@pytest.fixture
def storage_at(monkeypatch, tmp_path):
    """Point build_storage at a temp directory for the duration of a test."""
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    return storage


def test_check_passes_for_a_real_briefing(storage_at):
    storage_at.save("2026-08-21", PLAUSIBLE_AUDIO, "wav", _manifest())

    assert main(["check", "--briefing-id", "2026-08-21"]) == 0


def test_check_fails_when_nothing_generated_the_briefing(storage_at, capsys):
    assert main(["check", "--briefing-id", "2026-08-21"]) == 1
    assert "::error::No briefing for 2026-08-21" in capsys.readouterr().out


def test_check_fails_on_an_empty_briefing(storage_at, capsys):
    """The worst shape in the system: every TTS call failed, a manifest with
    zero turns was stored anyway, and because generation is idempotent that
    empty briefing then blocked its own replacement. It exits 0 and looks
    exactly like success."""
    storage_at.save(
        "2026-08-21", b"", "wav", _manifest(turns=[], duration_ms=0)
    )

    assert main(["check", "--briefing-id", "2026-08-21"]) == 1
    out = capsys.readouterr().out
    assert "::error::" in out
    assert "block its own replacement" in out


def test_check_fails_when_the_manifest_has_no_audio_behind_it(storage_at, capsys):
    """`--force` used to delete the audio before regenerating, so a failed
    retry stranded a manifest pointing at an object that no longer exists —
    200 on the manifest, 404 on the audio."""
    storage_at.save("2026-08-21", PLAUSIBLE_AUDIO, "wav", _manifest())
    storage_at.delete_audio("2026-08-21")

    assert main(["check", "--briefing-id", "2026-08-21"]) == 1
    assert "NO AUDIO" in capsys.readouterr().out


def test_check_reports_configuration_errors_separately(monkeypatch):
    """Exit 2, not 1. A misconfigured watchdog must not be indistinguishable
    from a missing briefing — the fixes are completely different."""
    def _boom():
        raise RuntimeError("R2 storage is not configured — missing R2_BUCKET")

    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", _boom, raising=True
    )

    assert main(["check", "--briefing-id", "2026-08-21"]) == 2


def test_check_does_not_report_a_read_failure_as_a_missing_briefing(
    storage_at, monkeypatch, capsys
):
    """R2 raises on anything that is not a genuine 404 precisely so a rotated
    or under-scoped token cannot be reported as "never generated". Crying
    wolf here would send the user to regenerate a briefing that already
    exists, spending real Gemini and TTS money."""
    def _boom(_briefing_id):
        raise RuntimeError("An error occurred (403) when calling GetObject")

    monkeypatch.setattr(storage_at, "load_manifest", _boom, raising=True)

    # 4, not 1. The watchdog gates its "No usable briefing — go and regenerate"
    # summary on exit 1, and a 403 is not evidence about the briefing at all.
    assert main(["check", "--briefing-id", "2026-08-21"]) == 4
    out = capsys.readouterr().out
    assert "Could not read briefing" in out
    assert "No briefing for" not in out


def test_check_reports_a_corrupt_manifest_as_a_storage_problem(
    storage_at, monkeypatch, capsys
):
    """A manifest that will not parse is not an absent one. Reporting it as
    "nothing generated it, the scheduler must have dropped the run" names the
    wrong cause and sends the operator to look in the wrong place."""
    from app.synthesis.storage import UnreadableManifestError

    def _corrupt(_briefing_id):
        raise UnreadableManifestError("manifest for 2026-08-21 is not JSON")

    monkeypatch.setattr(storage_at, "load_manifest", _corrupt, raising=True)

    assert main(["check", "--briefing-id", "2026-08-21"]) == 4
    out = capsys.readouterr().out
    assert "No briefing for" not in out


def test_check_fails_on_a_zero_byte_audio_object(storage_at, capsys):
    """`audio is None` is false for an empty payload, so check exited 0 while
    the phone got a 200 and nothing to play. An upload that dies mid-flight
    leaves exactly this."""
    storage_at.save("2026-08-21", b"", "wav", _manifest())

    assert main(["check", "--briefing-id", "2026-08-21"]) == 1
    assert "0 bytes" in capsys.readouterr().out


def test_check_fails_on_a_truncated_audio_object(storage_at):
    storage_at.save("2026-08-21", b"RIFF" + b"\0" * 40, "wav", _manifest())

    assert main(["check", "--briefing-id", "2026-08-21"]) == 1


@pytest.mark.parametrize("briefing_id", ["2026-01-99", "2026-02-30", "2026-13-01"])
def test_check_rejects_a_date_shaped_impossible_id(storage_at, briefing_id):
    """The shape check passed these, so generation would store a key under an
    id that is not a day — and retention, which parses ids as dates, could
    then never age it out."""
    assert main(["check", "--briefing-id", briefing_id]) == 2


def test_check_rejects_a_malformed_briefing_id(storage_at):
    assert main(["check", "--briefing-id", "not-a-date"]) == 2


def test_check_defaults_to_today_in_the_users_timezone(storage_at, monkeypatch):
    """Same id derivation as `generate`, deliberately. A watchdog that names
    the briefing differently from the generator reports a healthy morning as
    a failure every day the two disagree — this project has already had five
    separate date bugs."""
    monkeypatch.setattr(
        "app.cli._today_briefing_id", lambda _tz: "2026-08-21", raising=True
    )
    storage_at.save("2026-08-21", PLAUSIBLE_AUDIO, "wav", _manifest())

    assert main(["check"]) == 0
