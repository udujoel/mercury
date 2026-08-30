"""
Tests for `python -m app.cli generate`, and specifically for what --force is
allowed to destroy.

Generation is idempotent, so --force exists to replace a briefing that already
exists — a run that shipped degraded, or one with no audio in it. The danger is
that "replace" was implemented as "delete, then try to make a new one", which
is only equivalent when the second half succeeds.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.cli import main
from app.core.schemas import BriefingManifest, Turn
from app.synthesis.stitch import EmptyBriefingError
from app.synthesis.storage import LocalFilesystemStorage

# The real ExpiredCredentialsError, not a look-alike. app.cli identifies it by
# type name, and a stand-in defined here would pass whether or not the two
# names still agree — which is the whole thing being asserted.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from _google_auth import ExpiredCredentialsError  # noqa: E402


def _manifest(briefing_id: str = "2026-08-27") -> BriefingManifest:
    return BriefingManifest(
        briefing_id=briefing_id,
        generated_at=datetime.now(UTC),
        duration_ms=1000,
        audio_url=f"/briefing/{briefing_id}/audio",
        segments=[{"id": "cold_open", "label": "Good morning", "start_ms": 0}],
        turns=[Turn(speaker="A", segment="cold_open", text="Morning.")],
        sources=[],
    )


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """The CLI wired to temp storage and stub providers.

    Providers are stubbed rather than configured: constructing the real ones
    raises without credentials, and every test here fails or succeeds before
    a provider is ever called.
    """
    storage = LocalFilesystemStorage(base_dir=tmp_path)
    monkeypatch.setattr(
        "app.synthesis.storage.build_storage", lambda: storage, raising=True
    )
    monkeypatch.setattr(
        "app.providers.gemini.GeminiProvider", lambda: object(), raising=True
    )
    monkeypatch.setattr(
        "app.providers.google_tts.GoogleTTSProvider", lambda: object(), raising=True
    )
    return storage


def _fails_with(monkeypatch, exc: Exception) -> None:
    async def _boom(**_kwargs):
        raise exc

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _boom, raising=True
    )


def test_force_keeps_the_old_briefing_when_regeneration_fails(
    cli, monkeypatch
):
    """The finding. --force used to delete the audio BEFORE regenerating, so a
    failed retry stranded a manifest pointing at audio that no longer existed:
    the phone got 200 on the manifest and 404 on the audio, and that was the
    state of the day. A failed replacement must leave the previous briefing
    exactly as it was — stale audio beats no audio."""
    cli.save("2026-08-27", b"OLD AUDIO", "wav", _manifest())
    _fails_with(monkeypatch, RuntimeError("Gemini 503"))

    assert main(["generate", "--force", "--briefing-id", "2026-08-27"]) == 1

    assert cli.load_manifest("2026-08-27") is not None
    audio = cli.load_audio("2026-08-27")
    assert audio is not None
    assert audio[0] == b"OLD AUDIO"


def test_force_keeps_the_old_briefing_when_every_turn_fails_to_synthesize(
    cli, monkeypatch
):
    """Same guarantee for the total-TTS-failure path, which is the most likely
    reason anyone reaches for --force in the first place."""
    cli.save("2026-08-27", b"OLD AUDIO", "wav", _manifest())
    _fails_with(monkeypatch, EmptyBriefingError("no audible turns"))

    assert main(["generate", "--force", "--briefing-id", "2026-08-27"]) == 1

    audio = cli.load_audio("2026-08-27")
    assert audio is not None
    assert audio[0] == b"OLD AUDIO"


def test_without_force_an_existing_briefing_is_a_no_op(cli, monkeypatch):
    """The property the whole multi-trigger schedule depends on: a later run
    that finds a briefing already there costs one read and exits 0."""
    cli.save("2026-08-27", b"OLD AUDIO", "wav", _manifest())

    def _never(**_kwargs):
        raise AssertionError("generation must not run when a briefing exists")

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _never, raising=True
    )

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 0


def test_an_empty_briefing_failure_is_reported_distinctly(
    cli, monkeypatch, capsys
):
    """Nothing was stored, so the operator must not be told to re-run with
    --force — the action is to go and look at the TTS provider."""
    _fails_with(monkeypatch, EmptyBriefingError("no audible turns"))

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 1
    out = capsys.readouterr().out
    assert "NO AUDIBLE TURNS" in out
    assert "no --force" in out


def _succeeds_but_degraded(monkeypatch, storage, failures):
    """A generation that stores a real briefing while some personal source
    failed — which is exactly what CLAUDE.md rule 7 asks the pipeline to do,
    and exactly the run that used to exit 0 and tell nobody."""
    from app.pipeline import generate as generate_module

    async def _ok(*, briefing_id, **_kwargs):
        manifest = _manifest(briefing_id)
        storage.save(briefing_id, b"REAL AUDIO", "wav", manifest)
        generate_module._record_failed_sources(failures)
        return manifest

    monkeypatch.setattr(
        "app.pipeline.generate.generate_briefing", _ok, raising=True
    )


def test_a_degraded_briefing_fails_the_run_but_still_ships(
    cli, monkeypatch, capsys
):
    """The 2026-08-27 lesson. A briefing that shipped without the user's inbox
    exited 0, so Actions went green and no email was sent, and it was found
    days later by reading `gh run list`. The audio must still be stored — rule
    7 — but the run must stop claiming success."""
    _succeeds_but_degraded(monkeypatch, cli, {"email": RuntimeError("Gmail 500")})

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 3

    out = capsys.readouterr().out
    assert "::warning::" in out
    assert "::error::" in out
    assert "DEGRADED" in out

    # Shipped anyway: the phone has something to play.
    audio = cli.load_audio("2026-08-27")
    assert audio is not None and audio[0] == b"REAL AUDIO"


def test_allow_degraded_opts_back_out(cli, monkeypatch):
    _succeeds_but_degraded(monkeypatch, cli, {"calendar": RuntimeError("boom")})

    assert (
        main(["generate", "--allow-degraded", "--briefing-id", "2026-08-27"]) == 0
    )


def test_a_healthy_briefing_is_still_a_clean_zero(cli, monkeypatch, capsys):
    """The guard against the opposite mistake: turning every run red teaches
    the user to ignore the email, which is the same outcome as sending none."""
    _succeeds_but_degraded(monkeypatch, cli, {})

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 0
    out = capsys.readouterr().out
    assert "::warning::" not in out
    assert "::error::" not in out


def test_an_expired_google_token_says_so_and_names_the_permanent_fix(
    cli, monkeypatch, capsys
):
    """The most likely production failure in this deployment, and it arrives
    as a bare invalid_grant that says nothing. While the consent screen is in
    Testing mode Google expires refresh tokens after 7 days, so this recurs
    weekly until it is fixed — the error has to be recognisable on sight."""
    _succeeds_but_degraded(
        monkeypatch,
        cli,
        {
            "email": ExpiredCredentialsError("token could not be refreshed"),
            "calendar": ExpiredCredentialsError("token could not be refreshed"),
        },
    )

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 3

    out = capsys.readouterr().out
    assert "EXPIRED" in out
    assert "Testing mode" in out
    assert "Publish app" in out
    assert "GOOGLE_OAUTH_TOKEN_JSON" in out


def test_a_transient_failure_is_not_reported_as_an_expired_token(
    cli, monkeypatch, capsys
):
    """Identified by exception TYPE, not by message text. A Gmail 500 is not a
    reason to go and republish an OAuth consent screen."""
    _succeeds_but_degraded(monkeypatch, cli, {"email": RuntimeError("Gmail 500")})

    assert main(["generate", "--briefing-id", "2026-08-27"]) == 3
    assert "Testing mode" not in capsys.readouterr().out
