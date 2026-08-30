"""
Tests for `python -m app.cli generate`, and specifically for what --force is
allowed to destroy.

Generation is idempotent, so --force exists to replace a briefing that already
exists — a run that shipped degraded, or one with no audio in it. The danger is
that "replace" was implemented as "delete, then try to make a new one", which
is only equivalent when the second half succeeds.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.cli import main
from app.core.schemas import BriefingManifest, Turn
from app.synthesis.stitch import EmptyBriefingError
from app.synthesis.storage import LocalFilesystemStorage


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
